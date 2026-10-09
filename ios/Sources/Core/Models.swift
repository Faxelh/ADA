import Foundation

// Lecture tolérante des valeurs JSON venues de Python.
func jsonString(_ value: Any?) -> String? {
    if let text = value as? String { return text }
    if let number = value as? NSNumber { return number.stringValue }
    return nil
}

func jsonDouble(_ value: Any?) -> Double? {
    if let number = value as? NSNumber, number.doubleValue.isFinite { return number.doubleValue }
    if let text = value as? String, let number = Double(text), number.isFinite { return number }
    return nil
}

func jsonBool(_ value: Any?) -> Bool {
    if let number = value as? NSNumber { return number.boolValue }
    return false
}

/// Un morceau de la bibliothèque (ou d'une playlist, du Coffre…).
struct Track: Identifiable, Hashable {
    let id: String
    let uid: String?
    let title: String
    let path: String
    let duration: Double?
    let filesize: Double?
    let thumbnail: String?
    let date: String
    let uploader: String
    let platform: String
    let exists: Bool
    /// Données d'origine, renvoyées telles quelles au moteur Python.
    let raw: [String: Any]

    init(_ dict: [String: Any]) {
        raw = dict
        uid = jsonString(dict["uid"])
        path = jsonString(dict["filepath"]) ?? ""
        id = uid ?? path
        title = jsonString(dict["title"]) ?? ""
        duration = jsonDouble(dict["duration"])
        filesize = jsonDouble(dict["filesize"])
        let thumb = jsonString(dict["thumbnail"]) ?? ""
        thumbnail = thumb.isEmpty ? nil : thumb
        date = jsonString(dict["date"]) ?? ""
        uploader = jsonString(dict["uploader"]) ?? ""
        platform = jsonString(dict["platform"]) ?? ""
        if dict["exists"] != nil {
            exists = jsonBool(dict["exists"])
        } else {
            exists = !path.isEmpty && FileManager.default.fileExists(atPath: path)
        }
    }

    static func == (lhs: Track, rhs: Track) -> Bool {
        lhs.id == rhs.id && lhs.path == rhs.path && lhs.title == rhs.title && lhs.filesize == rhs.filesize
            && lhs.exists == rhs.exists
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
        hasher.combine(path)
    }

    var fileURL: URL { URL(fileURLWithPath: path) }

    var meta: String {
        [Fmt.duration(duration), Fmt.size(filesize)].filter { !$0.isEmpty }.joined(separator: " · ")
    }

    var addedDate: Date? {
        Fmt.parseDate(date)
    }
}

struct Playlist: Identifiable, Hashable {
    let id: String
    let name: String
    let items: [Track]

    init(_ dict: [String: Any]) {
        id = jsonString(dict["uid"]) ?? UUID().uuidString
        name = jsonString(dict["name"]) ?? "Playlist"
        let raw = dict["items"] as? [[String: Any]] ?? []
        items = raw.map { Track($0) }
    }
}

/// Un import dans la file du moteur.
struct Job: Identifiable, Hashable {
    let id: String
    let title: String
    let url: String
    let status: String
    let label: String
    let fraction: Double
    let speed: Double?
    let message: String
    let thumbnail: String?
    let active: Bool

    init(_ dict: [String: Any]) {
        id = jsonString(dict["id"]) ?? UUID().uuidString
        url = jsonString(dict["url"]) ?? ""
        title = jsonString(dict["title"]) ?? url
        status = jsonString(dict["status"]) ?? ""
        label = jsonString(dict["label"]) ?? ""
        fraction = jsonDouble(dict["fraction"]) ?? 0
        speed = jsonDouble(dict["speed"])
        message = jsonString(dict["message"]) ?? ""
        let thumb = jsonString(dict["thumbnail"]) ?? ""
        thumbnail = thumb.isEmpty ? nil : thumb
        active = jsonBool(dict["active"])
    }

    var isPaused: Bool { status == "paused" }
    var isFailed: Bool { status == "failed" }

    var statusText: String {
        if status == "downloading" {
            var text = "\(Int(fraction * 100)) %"
            let speedText = Fmt.speed(speed)
            if !speedText.isEmpty { text += " · " + speedText }
            return text
        }
        if isFailed && !message.isEmpty { return L(message) }
        return L(label)
    }
}

/// Un résultat de recherche ou des tendances.
struct WebResult: Identifiable, Hashable {
    let id: String
    let url: String
    let title: String
    let uploader: String
    let duration: Double?
    let thumbnail: String?
    let views: Double?

    init(_ dict: [String: Any]) {
        url = jsonString(dict["url"]) ?? ""
        id = url
        title = jsonString(dict["title"]) ?? url
        uploader = jsonString(dict["uploader"]) ?? ""
        duration = jsonDouble(dict["duration"])
        let thumb = jsonString(dict["thumbnail"]) ?? ""
        thumbnail = thumb.isEmpty ? nil : thumb
        views = jsonDouble(dict["view_count"])
    }

    var meta: String {
        var parts: [String] = []
        if !uploader.isEmpty { parts.append(uploader) }
        let time = Fmt.duration(duration)
        if !time.isEmpty { parts.append(time) }
        if let views = views, views > 0 { parts.append(L("{count} vues", ["count": Fmt.count(views)])) }
        return parts.joined(separator: " · ")
    }
}

/// Écrans de l'onglet Playlists.
enum Route: Hashable {
    case favorites
    case recent
    case vault
    case playlist(String)
}

enum AppTab: Hashable {
    case home, search, playlists, trending, settings
}
