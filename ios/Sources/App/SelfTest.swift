import Foundation

/// Vérification automatique lancée par GitHub dans le simulateur iPhone
/// (variable ADA_SELFTEST=1) : démarre Python et le moteur, vérifie yt-dlp,
/// JavaScriptCore et une vraie recherche, écrit le résultat puis quitte.
enum SelfTest {
    static func runIfRequested() {
        guard ProcessInfo.processInfo.environment["ADA_SELFTEST"] == "1" else { return }
        var report: [String: Any] = [:]
        let engine = PythonEngine.shared
        let start = engine.start(language: "fr")
        report["init"] = start
        var ok = start["error"] == nil && jsonString(start["ytdlp"]) != nil
        if ok {
            let search = engine.call("search", ["query": "lofi", "limit": 3])
            let count = (search["results"] as? [Any])?.count ?? 0
            report["search_results"] = count
            if let error = search["error"] { report["search_error"] = error }
            report["library"] = (engine.call("library")["entries"] as? [Any])?.count ?? -1
            ok = ok && search["error"] == nil
        }
        report["ok"] = ok
        let data = (try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])) ?? Data()
        let text = String(data: data, encoding: .utf8) ?? "{}"
        print("ADA-SELFTEST-RESULT " + text)
        fflush(stdout)
        exit(ok ? 0 : 1)
    }
}
