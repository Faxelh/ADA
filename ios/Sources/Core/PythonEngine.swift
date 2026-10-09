import Foundation

/// Le moteur Python d'ADA'S (yt-dlp, file des imports, bibliothèque),
/// intégré à l'app. Swift lui parle en JSON via player.bridge.dispatch.
final class PythonEngine {
    static let shared = PythonEngine()

    private(set) var started = false
    private(set) var startError: String?

    /// Appels rapides (état de la file, bibliothèque…) : un seul à la fois.
    let queue = DispatchQueue(label: "ada.python", qos: .userInitiated)
    /// Appels longs (recherche, tendances) : en parallèle, sans bloquer les autres.
    let slowQueue = DispatchQueue(label: "ada.python.slow", qos: .userInitiated, attributes: .concurrent)

    private init() {}

    static var documentsURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    static var dataURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    }

    static var cacheURL: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("ADA")
    }

    /// Démarre Python puis le moteur. À appeler hors du fil principal.
    @discardableResult
    func start(language: String) -> [String: Any] {
        if started {
            return call("info")
        }
        guard let resources = Bundle.main.resourcePath else {
            startError = "Dossier de l'app introuvable."
            return ["error": startError ?? ""]
        }
        let home = resources + "/python"
        let lib = home + "/lib"
        let names = (try? FileManager.default.contentsOfDirectory(atPath: lib)) ?? []
        let version = names.first(where: { $0.hasPrefix("python3") }) ?? "python3.13"
        let paths = [
            lib + "/" + version,
            lib + "/" + version + "/lib-dynload",
            resources + "/app",
            resources + "/app_packages",
        ].joined(separator: "\n")

        for url in [PythonEngine.documentsURL, PythonEngine.dataURL, PythonEngine.cacheURL] {
            try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        }

        if let error = ada_python_start(home, paths) {
            let message = String(cString: error)
            ada_python_free(error)
            startError = message
            return ["error": message]
        }
        started = true
        let result = call("init", [
            "documents": PythonEngine.documentsURL.path,
            "data": PythonEngine.dataURL.path,
            "cache": PythonEngine.cacheURL.path,
            "language": language,
        ])
        if let error = result["error"] as? String {
            startError = error
        }
        return result
    }

    /// Appel synchrone (depuis n'importe quel fil sauf le principal de préférence).
    func call(_ op: String, _ args: [String: Any] = [:]) -> [String: Any] {
        guard started else {
            return ["error": startError ?? "Python n'est pas démarré"]
        }
        let payload: String
        if JSONSerialization.isValidJSONObject(args),
           let data = try? JSONSerialization.data(withJSONObject: args),
           let text = String(data: data, encoding: .utf8) {
            payload = text
        } else {
            payload = "{}"
        }
        guard let raw = ada_python_call(op, payload) else {
            return ["error": "Réponse vide"]
        }
        let text = String(cString: raw)
        ada_python_free(raw)
        guard let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data),
              let dict = object as? [String: Any] else {
            return ["error": text]
        }
        return dict
    }

    /// Appel asynchrone : le résultat revient sur le fil principal.
    func run(_ op: String, _ args: [String: Any] = [:], slow: Bool = false,
             completion: (([String: Any]) -> Void)? = nil) {
        let target = slow ? slowQueue : queue
        target.async {
            let result = self.call(op, args)
            if let completion = completion {
                DispatchQueue.main.async { completion(result) }
            }
        }
    }
}
