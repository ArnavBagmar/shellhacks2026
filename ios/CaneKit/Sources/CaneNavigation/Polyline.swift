import Foundation

/// Google's encoded-polyline algorithm at precision 1e5 (step-12 brief
/// H2). Both directions live here: `decode` reads the Routes API
/// response, `encode` lets tests and `nav-cli` build real geometry
/// instead of pasting opaque strings.
public enum Polyline {
    /// Coordinates are transmitted as integers of 1e-5 degrees (~1.1 m).
    public static let precision: Double = 1e5

    private static let asciiOffset = 63
    private static let chunkMask = 0x1F
    private static let continuationBit = 0x20
    private static let maxShift = 32

    /// Decodes an encoded polyline into points.
    /// - Throws: `NavigationError.decoding` if the string ends mid-number
    ///   or mid-pair, or contains a character below the 63 offset.
    public static func decode(_ encoded: String) throws -> [LatLng] {
        let scalars = Array(encoded.unicodeScalars)
        var index = 0
        var latitudeE5 = 0
        var longitudeE5 = 0
        var points: [LatLng] = []
        points.reserveCapacity(scalars.count / 4)

        while index < scalars.count {
            latitudeE5 += try decodeValue(scalars, at: &index)
            guard index < scalars.count else {
                throw NavigationError.decoding("encoded polyline ended after a latitude")
            }
            longitudeE5 += try decodeValue(scalars, at: &index)
            points.append(
                LatLng(
                    latitude: Double(latitudeE5) / precision,
                    longitude: Double(longitudeE5) / precision
                )
            )
        }
        return points
    }

    /// Encodes points into the wire format. Round-trips with `decode`
    /// up to the 1e-5 quantisation.
    public static func encode(_ points: [LatLng]) -> String {
        var latitudeE5 = 0
        var longitudeE5 = 0
        var encoded = ""

        for point in points {
            let nextLatitude = Int((point.latitude * precision).rounded())
            let nextLongitude = Int((point.longitude * precision).rounded())
            encoded += encodeValue(nextLatitude - latitudeE5)
            encoded += encodeValue(nextLongitude - longitudeE5)
            latitudeE5 = nextLatitude
            longitudeE5 = nextLongitude
        }
        return encoded
    }

    /// One zig-zag-encoded delta: 5 bits per character, low chunk first,
    /// continuation flagged by bit 0x20.
    private static func decodeValue(_ scalars: [UnicodeScalar], at index: inout Int) throws -> Int {
        var result = 0
        var shift = 0

        while true {
            guard index < scalars.count else {
                throw NavigationError.decoding("encoded polyline ended mid-value")
            }
            let value = Int(scalars[index].value) - asciiOffset
            guard value >= 0 else {
                throw NavigationError.decoding("invalid character in encoded polyline")
            }
            index += 1
            result |= (value & chunkMask) << shift
            shift += 5
            if value & continuationBit == 0 { break }
            guard shift < maxShift else {
                throw NavigationError.decoding("over-long value in encoded polyline")
            }
        }
        return result & 1 == 1 ? ~(result >> 1) : result >> 1
    }

    private static func encodeValue(_ delta: Int) -> String {
        var value = delta < 0 ? ~(delta << 1) : delta << 1
        var encoded = ""

        while value >= continuationBit {
            encoded.unicodeScalars.append(
                UnicodeScalar(UInt8((value & chunkMask) | continuationBit) + UInt8(asciiOffset))
            )
            value >>= 5
        }
        encoded.unicodeScalars.append(UnicodeScalar(UInt8(value) + UInt8(asciiOffset)))
        return encoded
    }
}
