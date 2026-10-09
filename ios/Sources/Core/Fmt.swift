import Foundation

/// Mise en forme des durées, tailles et nombres selon la langue.
enum Fmt {
    static func duration(_ seconds: Double?) -> String {
        guard let seconds = seconds, seconds.isFinite, seconds > 0 else { return "" }
        let total = Int(seconds.rounded())
        let hours = total / 3600
        let minutes = (total % 3600) / 60
        let secs = total % 60
        if hours > 0 {
            return String(format: "%d:%02d:%02d", hours, minutes, secs)
        }
        return String(format: "%d:%02d", minutes, secs)
    }

    /// Temps de lecture (toujours affiché, même à 0).
    static func clock(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds > 0 else { return "0:00" }
        return duration(seconds)
    }

    static func size(_ bytes: Double?) -> String {
        guard var value = bytes, value.isFinite, value > 0 else { return "" }
        let english = Lang.code == "en"
        let units = english ? ["B", "KB", "MB", "GB"] : ["o", "Ko", "Mo", "Go"]
        var index = 0
        while value >= 1024 && index < units.count - 1 {
            value /= 1024
            index += 1
        }
        if index == 0 {
            return "\(Int(value)) \(units[0])"
        }
        var number = String(format: "%.1f", value)
        if !english {
            number = number.replacingOccurrences(of: ".", with: ",")
        }
        return number + " " + units[index]
    }

    static func speed(_ bytesPerSecond: Double?) -> String {
        let text = size(bytesPerSecond)
        return text.isEmpty ? "" : text + "/s"
    }

    static func count(_ value: Double) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.maximumFractionDigits = 0
        formatter.locale = Locale(identifier: Lang.code == "en" ? "en_US" : "fr_FR")
        return formatter.string(from: NSNumber(value: value)) ?? "\(Int(value))"
    }

    static func parseDate(_ text: String) -> Date? {
        guard !text.isEmpty else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "fr_FR")
        formatter.dateFormat = "dd/MM/yyyy HH:mm"
        return formatter.date(from: text)
    }
}
