#if os(iOS)
import AVFoundation
import Foundation
import os

/// Reads the TrueDepth sensor and reports how far away whatever is straight
/// ahead actually is, in metres.
///
/// This is the only place in the project that produces a *measured* distance.
/// Depth Anything (desktop preview) and the X4 path both produce relative or
/// band-only range and are forbidden from stating metres; TrueDepth is an
/// infrared depth sensor, so `.absolute` readings are real.
///
/// Three things keep it honest:
/// - **Filtering is off.** `isFilteringEnabled` interpolates over the pixels
///   the sensor could not resolve, which would fill holes with invented
///   values and inflate coverage. With it off, a hole stays NaN and the
///   coverage figure means what it says.
/// - **Accuracy is checked per frame.** `AVDepthData` reports `.relative`
///   when it has shape but no trustworthy scale; `DepthSample.isTrustworthy`
///   refuses those.
/// - **Only the probe region is copied.** Copying a whole 640x480 map every
///   frame is ~1.2 MB at 30 fps for values nobody reads.
public final class TrueDepthProbe: NSObject, AVCaptureDepthDataOutputDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var sample: DepthSample?
    /// The newest frame itself, kept (not copied) so a question's photo can
    /// take the whole map with it; copying it every frame would cost
    /// ~1.2 MB at 30 fps for maps nobody reads.
    private var newest: (data: AVDepthData, accuracy: DepthAccuracy, timestampMs: Int64)?
    private var onSample: (@Sendable (DepthSample) -> Void)?
    private let logger = Logger(subsystem: "cane", category: "depth")
    private var loggedFirst = false

    public override init() { super.init() }

    /// The most recent reading, or nil before the first frame arrives.
    public var latest: DepthSample? { lock.withLock { sample } }

    /// Called on the depth queue for every frame. Keep it quick.
    public func onReading(_ handler: @escaping @Sendable (DepthSample) -> Void) {
        lock.withLock { onSample = handler }
    }

    public func depthDataOutput(_ output: AVCaptureDepthDataOutput,
                                didOutput depthData: AVDepthData,
                                timestamp: CMTime,
                                connection: AVCaptureConnection) {
        // The sensor may hand back disparity (1/metres) rather than depth.
        let depth = depthData.depthDataType == kCVPixelFormatType_DepthFloat32
            ? depthData
            : depthData.converting(toDepthDataType: kCVPixelFormatType_DepthFloat32)

        let accuracy: DepthAccuracy = depth.depthDataAccuracy == .absolute ? .absolute : .relative
        let ms = Int64(CMTimeGetSeconds(timestamp) * 1_000)
        guard let reading = Self.read(depth.depthDataMap, accuracy: accuracy, timestampMs: ms) else { return }

        let handler = lock.withLock { () -> (@Sendable (DepthSample) -> Void)? in
            sample = reading
            newest = (depth, accuracy, ms)
            return onSample
        }
        if !loggedFirst {
            loggedFirst = true
            logger.info("""
                truedepth first reading: \(reading.metres, format: .fixed(precision: 2)) m, \
                coverage \(reading.coverage, format: .fixed(precision: 2)), accuracy \(accuracy.rawValue)
                """)
        }
        handler?(reading)
    }

    /// The whole newest map, copied once, in the sensor's own orientation.
    public func latestMap() -> DepthMap? {
        guard let newest = lock.withLock({ newest }) else { return nil }
        let map = newest.data.depthDataMap
        CVPixelBufferLockBaseAddress(map, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(map, .readOnly) }
        let width = CVPixelBufferGetWidth(map)
        let height = CVPixelBufferGetHeight(map)
        guard width > 0, height > 0, let base = CVPixelBufferGetBaseAddress(map) else { return nil }
        let rowBytes = CVPixelBufferGetBytesPerRow(map)
        var values = [Float](repeating: .nan, count: width * height)
        for y in 0..<height {
            let row = base.advanced(by: y * rowBytes).assumingMemoryBound(to: Float32.self)
            for x in 0..<width { values[y * width + x] = row[x] }
        }
        return DepthMap(values: values, width: width, height: height,
                        accuracy: newest.accuracy, timestampMs: newest.timestampMs)
    }

    /// Copies only the central probe region out of the depth map.
    static func read(_ map: CVPixelBuffer, accuracy: DepthAccuracy, timestampMs: Int64) -> DepthSample? {
        CVPixelBufferLockBaseAddress(map, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(map, .readOnly) }
        let width = CVPixelBufferGetWidth(map)
        let height = CVPixelBufferGetHeight(map)
        guard width > 0, height > 0, let base = CVPixelBufferGetBaseAddress(map) else { return nil }

        let box = DepthProbe.centreRegion(width: width, height: height)
        let rowBytes = CVPixelBufferGetBytesPerRow(map)
        let regionWidth = box.x1 - box.x0
        let regionHeight = box.y1 - box.y0
        guard regionWidth > 0, regionHeight > 0 else { return nil }

        var values = [Float](repeating: .nan, count: regionWidth * regionHeight)
        for y in 0..<regionHeight {
            // Rows are padded to bytesPerRow, which is not always width * 4.
            let row = base.advanced(by: (box.y0 + y) * rowBytes).assumingMemoryBound(to: Float32.self)
            for x in 0..<regionWidth {
                values[y * regionWidth + x] = row[box.x0 + x]
            }
        }
        return DepthProbe.sample(values: values, width: regionWidth, height: regionHeight,
                                 region: (0, regionWidth, 0, regionHeight),
                                 accuracy: accuracy, timestampMs: timestampMs)
    }
}
#endif
