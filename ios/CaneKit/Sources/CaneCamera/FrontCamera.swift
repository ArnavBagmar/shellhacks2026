#if os(iOS)
import AVFoundation
import CoreImage
import Foundation
import os

/// Gets every frame the front camera delivers, on the camera queue. P1's
/// YOLO plugs in here; it must be quick or drop frames (late frames are
/// discarded, never queued).
public protocol FrameConsumer: AnyObject {
    func frontCamera(didOutput sampleBuffer: CMSampleBuffer, hfovDeg: Double)
}

/// The one front-camera session, shared by perception (P1), scene questions
/// and sign reading.
///
/// Settings that keep bearings true (see `CameraGeometry`):
/// - **mirroring off** -- a selfie-mirrored frame flips every bearing's sign;
/// - **Center Stage off** and **stabilization off** -- both crop and pan the
///   frame, so a pixel column stops meaning a fixed angle;
/// - frames rotated upright via `RotationCoordinator`, so the horizontal
///   axis of the image is the horizontal axis of the world in either mount.
public final class FrontCamera: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    public struct Snapshot: Sendable {
        public let jpeg: Data
        public let hfovDeg: Double
        public let width: Int
        public let height: Int
    }

    public enum CameraError: Error {
        case noFrontCamera
        case cannotAddInput
        case cannotAddOutput
    }

    private let session = AVCaptureSession()
    private let output = AVCaptureVideoDataOutput()
    private let queue = DispatchQueue(label: "cane.camera", qos: .userInitiated)
    private let ciContext = CIContext()
    private let lock = NSLock()
    private var latest: CVPixelBuffer?
    private var consumers: [WeakConsumer] = []
    private var coordinator: AVCaptureDevice.RotationCoordinator?
    private var rotationObservation: NSKeyValueObservation?
    private var sensorFOVDeg = 70.0
    private var sensorAspect = 4.0 / 3.0
    private var configured = false
    private let logger = Logger(subsystem: "cane", category: "camera")

    public override init() {
        super.init()
    }

    public static func requestPermission() async -> Bool {
        await AVCaptureDevice.requestAccess(for: .video)
    }

    public func start() throws {
        if !configured {
            try configure()
            configured = true
        }
        queue.async { [session] in
            if !session.isRunning { session.startRunning() }
        }
    }

    public func stop() {
        queue.async { [session] in
            if session.isRunning { session.stopRunning() }
        }
    }

    public func addConsumer(_ consumer: FrameConsumer) {
        lock.withLock { consumers.append(WeakConsumer(consumer)) }
    }

    /// The newest frame as an upright JPEG, longest side <= `maxSide`.
    public func snapshotJPEG(maxSide: Int = 1_280, quality: Double = 0.7) -> Snapshot? {
        guard let buffer = lock.withLock({ latest }) else { return nil }
        var image = CIImage(cvPixelBuffer: buffer)
        let extent = image.extent
        let scale = min(1, Double(maxSide) / Double(max(extent.width, extent.height)))
        if scale < 1 {
            image = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        }
        let options = [CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String): quality]
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let jpeg = ciContext.jpegRepresentation(of: image, colorSpace: space, options: options)
        else { return nil }
        let width = Int(image.extent.width.rounded())
        let height = Int(image.extent.height.rounded())
        return Snapshot(jpeg: jpeg, hfovDeg: hfov(width: width, height: height), width: width, height: height)
    }

    // MARK: - Setup

    private func configure() throws {
        AVCaptureDevice.centerStageControlMode = .app
        AVCaptureDevice.isCenterStageEnabled = false
        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front) else {
            throw CameraError.noFrontCamera
        }
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        session.sessionPreset = session.canSetSessionPreset(.hd1280x720) ? .hd1280x720 : .high
        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else { throw CameraError.cannotAddInput }
        session.addInput(input)

        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        output.setSampleBufferDelegate(self, queue: queue)
        guard session.canAddOutput(output) else { throw CameraError.cannotAddOutput }
        session.addOutput(output)

        if let connection = output.connection(with: .video) {
            connection.automaticallyAdjustsVideoMirroring = false
            if connection.isVideoMirroringSupported { connection.isVideoMirrored = false }
            if connection.isVideoStabilizationSupported { connection.preferredVideoStabilizationMode = .off }
            if connection.isCameraIntrinsicMatrixDeliverySupported {
                connection.isCameraIntrinsicMatrixDeliveryEnabled = true
            }
        }

        let dims = CMVideoFormatDescriptionGetDimensions(device.activeFormat.formatDescription)
        let zoom = max(Double(device.videoZoomFactor), 1)
        let formatFOV = Double(device.activeFormat.videoFieldOfView)
        lock.withLock {
            sensorFOVDeg = 2 * atan(tan(formatFOV * .pi / 360) / zoom) * 180 / .pi
            sensorAspect = Double(max(dims.width, dims.height)) / Double(max(1, min(dims.width, dims.height)))
        }

        let coordinator = AVCaptureDevice.RotationCoordinator(device: device, previewLayer: nil)
        self.coordinator = coordinator
        applyRotation(coordinator.videoRotationAngleForHorizonLevelCapture)
        rotationObservation = coordinator.observe(\.videoRotationAngleForHorizonLevelCapture, options: [.new]) { [weak self] c, _ in
            self?.applyRotation(c.videoRotationAngleForHorizonLevelCapture)
        }
        logger.info("front camera: \(dims.width)x\(dims.height), fov \(formatFOV, format: .fixed(precision: 1)) deg, zoom \(zoom)")
    }

    private func applyRotation(_ angle: CGFloat) {
        queue.async { [output] in
            guard let connection = output.connection(with: .video),
                  connection.isVideoRotationAngleSupported(angle)
            else { return }
            connection.videoRotationAngle = angle
        }
    }

    private func hfov(width: Int, height: Int) -> Double {
        let (fov, aspect) = lock.withLock { (sensorFOVDeg, sensorAspect) }
        return CameraGeometry.imageHorizontalFOV(
            sensorLongSideFOVDeg: fov, sensorAspect: aspect,
            mount: width >= height ? .landscape : .portrait
        )
    }

    // MARK: - Frames

    public func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let hfov = hfov(width: CVPixelBufferGetWidth(buffer), height: CVPixelBufferGetHeight(buffer))
        let consumers = lock.withLock { () -> [FrameConsumer] in
            latest = buffer
            self.consumers.removeAll { $0.value == nil }
            return self.consumers.compactMap(\.value)
        }
        for consumer in consumers {
            consumer.frontCamera(didOutput: sampleBuffer, hfovDeg: hfov)
        }
    }
}

private struct WeakConsumer {
    weak var value: FrameConsumer?
    init(_ value: FrameConsumer) { self.value = value }
}
#endif
