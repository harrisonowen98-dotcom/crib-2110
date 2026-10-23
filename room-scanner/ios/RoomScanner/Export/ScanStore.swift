import Foundation
import SwiftUI
import CryptoKit
import UIKit

struct ScanItem: Identifiable, Equatable {
    var id: String { folder }
    let folder: String          // <name>_v<N>_<yyyy-MM-dd>
    let name: String
    let version: Int
    let date: String
    let url: URL
    var frames: Int
    var rooms: Int
}

/// Library of scans in Documents/Scans (visible in the Files app). Every scan is a NEW version folder; nothing is ever overwritten.
@MainActor
final class ScanStore: ObservableObject {
    @Published private(set) var scans: [ScanItem] = []
    let root: URL

    init(root: URL? = nil) {
        self.root = root ?? FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("Scans", isDirectory: true)
        try? FileManager.default.createDirectory(at: self.root, withIntermediateDirectories: true)
        reload()
    }

    nonisolated static func dateString(_ d: Date = Date()) -> String {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX"); return f.string(from: d)
    }
    nonisolated static func sanitized(_ name: String) -> String {
        let bad = CharacterSet(charactersIn: "/\\:*?\"<>|").union(.controlCharacters)
        let s = name.components(separatedBy: bad).joined().trimmingCharacters(in: .whitespaces).replacingOccurrences(of: " ", with: "-")
        return s.isEmpty ? "Room" : s
    }
    nonisolated static func folderName(name: String, version: Int, date: Date = Date()) -> String { "\(sanitized(name))_v\(version)_\(dateString(date))" }

    /// Next free version number for a name, looking at existing folders (so v4 follows v3 even after deletes of older ones).
    nonisolated static func nextVersion(name: String, existing: [String]) -> Int {
        let prefix = sanitized(name) + "_v"
        let nums = existing.compactMap { f -> Int? in
            guard f.hasPrefix(prefix) else { return nil }
            let rest = f.dropFirst(prefix.count)
            return Int(rest.prefix(while: { $0.isNumber }))
        }
        return (nums.max() ?? 0) + 1
    }

    func reload() {
        let fm = FileManager.default
        let dirs = (try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
        scans = dirs.compactMap { url -> ScanItem? in
            guard let data = try? Data(contentsOf: url.appendingPathComponent("manifest.json")),
                  let m = try? JSONDecoder().decode(Manifest.self, from: data) else { return nil }
            return ScanItem(folder: url.lastPathComponent, name: m.scanName, version: m.version, date: String(m.createdAt.prefix(10)), url: url, frames: m.counts.frames, rooms: m.counts.rooms)
        }.sorted { ($0.date, $0.version) > ($1.date, $1.version) }
    }

    /// Creates the (new) version folder skeleton for a scan about to start.
    nonisolated func createScanFolder(name: String) throws -> (url: URL, version: Int, folder: String) {
        let fm = FileManager.default
        let existing = (try? fm.contentsOfDirectory(atPath: root.path)) ?? []
        let v = Self.nextVersion(name: name, existing: existing)
        let folder = Self.folderName(name: name, version: v)
        let url = root.appendingPathComponent(folder, isDirectory: true)
        for sub in ["depth", "photos", "mesh", "rooms"] { try fm.createDirectory(at: url.appendingPathComponent(sub), withIntermediateDirectories: true) }
        return (url, v, folder)
    }

    func delete(_ item: ScanItem) { try? FileManager.default.removeItem(at: item.url); reload() }

    /// Rename = new display name inside manifest only; folder (and version history) stays intact.
    func rename(_ item: ScanItem, to newName: String) {
        let mp = item.url.appendingPathComponent("manifest.json")
        guard let data = try? Data(contentsOf: mp), var m = try? JSONDecoder().decode(Manifest.self, from: data) else { return }
        m.scanName = newName
        let enc = JSONEncoder(); enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        try? enc.encode(m).write(to: mp); reload()
    }

    func thumbnail(for item: ScanItem) -> UIImage? {
        let photos = item.url.appendingPathComponent("photos")
        guard let first = (try? FileManager.default.contentsOfDirectory(atPath: photos.path))?.sorted().dropFirst(2).first ?? (try? FileManager.default.contentsOfDirectory(atPath: photos.path))?.sorted().first else { return nil }
        guard let img = UIImage(contentsOfFile: photos.appendingPathComponent(first).path) else { return nil }
        let size = CGSize(width: 160, height: 120)
        return UIGraphicsImageRenderer(size: size).image { _ in img.draw(in: CGRect(origin: .zero, size: size)) }
    }

    /// One .zip per scan for the share sheet / AirDrop. Uses NSFileCoordinator(.forUploading) which zips a folder without dependencies.
    func zip(_ item: ScanItem) throws -> URL {
        var coordErr: NSError?; var result: URL?; var inner: Error?
        NSFileCoordinator().coordinate(readingItemAt: item.url, options: [.forUploading], error: &coordErr) { tmp in
            let dest = FileManager.default.temporaryDirectory.appendingPathComponent(item.folder + ".zip")
            try? FileManager.default.removeItem(at: dest)
            do { try FileManager.default.copyItem(at: tmp, to: dest); result = dest } catch { inner = error }
        }
        if let e = coordErr ?? inner { throw e }
        guard let r = result else { throw CocoaError(.fileWriteUnknown) }
        return r
    }
}

enum Hashing {
    static func sha256(of url: URL) -> String {
        guard let h = try? FileHandle(forReadingFrom: url) else { return String(repeating: "0", count: 64) }
        defer { try? h.close() }
        var hasher = SHA256()
        while let chunk = try? h.read(upToCount: 1 << 20), !chunk.isEmpty { hasher.update(data: chunk) }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    static func fileRecords(in root: URL) -> [FileRecord] {
        let fm = FileManager.default
        guard let e = fm.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey, .fileSizeKey]) else { return [] }
        var out: [FileRecord] = []
        for case let url as URL in e {
            let rv = try? url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
            guard rv?.isRegularFile == true, url.lastPathComponent != "manifest.json", url.lastPathComponent != ".DS_Store" else { continue }
            let rel = url.path.replacingOccurrences(of: root.path + "/", with: "")
            out.append(FileRecord(path: rel, bytes: rv?.fileSize ?? 0, sha256: sha256(of: url)))
        }
        return out.sorted { $0.path < $1.path }
    }
}
