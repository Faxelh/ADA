import AVFoundation
import LocalAuthentication
import SwiftUI
import UIKit

struct Toast: Identifiable, Equatable {
    enum Kind {
        case info, ok, warn, error
    }

    let id = UUID()
    let text: String
    let kind: Kind
}

/// État de l'app : bibliothèque, file des imports, Coffre, messages.
/// Tout passe par le moteur Python (PythonEngine) pour les données.
final class AppModel: ObservableObject {
    // Moteur
    @Published private(set) var engineReady = false
    @Published private(set) var engineError: String?
    @Published private(set) var engineInfo: [String: String] = [:]

    // Bibliothèque
    @Published private(set) var tracks: [Track] = []
    @Published private(set) var favorites: Set<String> = []
    @Published private(set) var favoriteTracks: [Track] = []
    @Published private(set) var vault: [Track] = []
    @Published private(set) var playlists: [Playlist] = []
    @Published private(set) var libraryLoaded = false

    // Imports
    @Published private(set) var jobs: [Job] = []
    @Published private(set) var compressing: Set<String> = []

    // Navigation et sécurité
    @Published var tab: AppTab = .home
    @Published var playlistPath: [Route] = []
    @Published var pendingSearch: String?
    @Published private(set) var vaultUnlocked = false
    @Published private(set) var appLocked = false

    @Published var toast: Toast?

    weak var player: AudioPlayer?

    private let engine = PythonEngine.shared
    private var timer: Timer?
    private var polling = false
    private var libraryRev = -1
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid

    init() {
        Lang.apply(UserDefaults.standard.string(forKey: "language") ?? "system")
        if UserDefaults.standard.bool(forKey: "appLock") {
            appLocked = true
        }
        startEngine()
    }

    func attach(_ player: AudioPlayer) {
        self.player = player
        player.onMessage = { [weak self] text in
            self?.show(text, .info)
        }
    }

    // MARK: - Moteur

    private func startEngine() {
        let language = Lang.code
        engine.queue.async { [weak self] in
            let result = PythonEngine.shared.start(language: language)
            DispatchQueue.main.async {
                guard let self = self else { return }
                if let error = result["error"] as? String {
                    self.engineError = error
                    return
                }
                var info: [String: String] = [:]
                for key in ["python", "ytdlp"] {
                    if let value = jsonString(result[key]) { info[key] = value }
                }
                info["javascript"] = jsonBool(result["javascript"]) ? "1" : "0"
                self.engineInfo = info
                self.engineReady = true
                self.applyEngineSettings()
                self.refreshLibrary()
                self.startPolling()
            }
        }
    }

    func setEngineLanguage() {
        engine.run("language", ["language": Lang.code])
    }

    private func startPolling() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.poll()
        }
    }

    private func poll() {
        guard engineReady, !polling else { return }
        polling = true
        engine.run("poll") { [weak self] result in
            guard let self = self else { return }
            self.polling = false
            guard result["error"] == nil else { return }
            let newJobs = (result["jobs"] as? [[String: Any]] ?? []).map { Job($0) }
            if newJobs != self.jobs {
                self.jobs = newJobs
            }
            for event in result["events"] as? [[String: Any]] ?? [] {
                self.handle(event)
            }
            let rev = Int(jsonDouble(result["rev"]) ?? 0)
            if rev != self.libraryRev {
                self.refreshLibrary()
            }
            self.updateIdleTimer(active: jsonBool(result["active"]))
        }
    }

    private func handle(_ event: [String: Any]) {
        let kind = jsonString(event["kind"]) ?? ""
        let title = jsonString(event["title"]) ?? ""
        switch kind {
        case "finished":
            show(L("Importé : {title}", ["title": title]), .ok)
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        case "failed":
            show(L("Échec : {title}", ["title": title]), .error)
        case "network":
            if jsonBool(event["online"]) {
                show(L("Connexion rétablie : reprise des imports."), .ok)
            } else {
                show(L("Pas de connexion internet : les imports reprendront tout seuls."), .warn)
            }
        default:
            break
        }
    }

    func refreshLibrary() {
        guard engineReady else { return }
        engine.run("library") { [weak self] result in
            guard let self = self, result["error"] == nil else { return }
            self.libraryRev = Int(jsonDouble(result["rev"]) ?? 0)
            let entries = (result["entries"] as? [[String: Any]] ?? []).map { Track($0) }
            if entries != self.tracks { self.tracks = entries }
            let favs = Set((result["favorites"] as? [Any] ?? []).compactMap { jsonString($0) })
            if favs != self.favorites { self.favorites = favs }
            let favItems = (result["favorite_items"] as? [[String: Any]] ?? []).map { Track($0) }
            if favItems != self.favoriteTracks { self.favoriteTracks = favItems }
            let vaultItems = (result["vault"] as? [[String: Any]] ?? []).map { Track($0) }
            if vaultItems != self.vault { self.vault = vaultItems }
            let lists = (result["playlists"] as? [[String: Any]] ?? []).map { Playlist($0) }
            if lists != self.playlists { self.playlists = lists }
            self.libraryLoaded = true
        }
    }

    private func updateIdleTimer(active: Bool) {
        let keepAwake = UserDefaults.standard.object(forKey: "keepAwake") as? Bool ?? true
        let wanted = keepAwake && active
        if UIApplication.shared.isIdleTimerDisabled != wanted {
            UIApplication.shared.isIdleTimerDisabled = wanted
        }
    }

    // MARK: - Messages

    func show(_ text: String, _ kind: Toast.Kind = .info) {
        let toast = Toast(text: text, kind: kind)
        withAnimation(.spring(response: 0.35, dampingFraction: 0.85)) {
            self.toast = toast
        }
        let delay: Double = kind == .error ? 5 : 3
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self = self, self.toast?.id == toast.id else { return }
            withAnimation(.easeOut(duration: 0.25)) {
                self.toast = nil
            }
        }
    }

    // MARK: - Import

    func importClipboard() {
        let text = UIPasteboard.general.string ?? ""
        guard text.contains("http") || text.contains("www.") || text.contains("youtu") else {
            show(L("Copiez d'abord un lien YouTube (ou d'un autre site), puis touchez « Coller un lien »."), .warn)
            return
        }
        importText(text)
    }

    func importText(_ text: String) {
        guard engineReady else {
            show(L("Le moteur démarre, réessayez dans un instant."), .warn)
            return
        }
        engine.run("import_text", ["text": text]) { [weak self] result in
            guard let self = self else { return }
            let count = Int(jsonDouble(result["count"]) ?? 0)
            if count > 0 {
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                self.show(L("{count} import(s) lancé(s).", ["count": "\(count)"]), .ok)
                self.poll()
            } else {
                self.show(L("Aucun lien trouvé dans le texte copié."), .warn)
            }
        }
    }

    func importResult(_ result: WebResult) {
        guard engineReady else {
            show(L("Le moteur démarre, réessayez dans un instant."), .warn)
            return
        }
        engine.run("import", ["items": [["url": result.url, "title": result.title]]]) { [weak self] _ in
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            self?.show(L("Import lancé : {title}", ["title": result.title]), .ok)
            self?.poll()
        }
    }

    func jobAction(_ action: String, _ job: Job? = nil) {
        var args: [String: Any] = ["action": action]
        if let job = job { args["id"] = job.id }
        engine.run("job", args) { [weak self] _ in self?.poll() }
    }

    // MARK: - Recherche

    func search(_ query: String, completion: @escaping ([WebResult]?) -> Void) {
        engine.run("search", ["query": query, "limit": 25], slow: true) { result in
            if result["error"] != nil {
                completion(nil)
                return
            }
            completion((result["results"] as? [[String: Any]] ?? []).map { WebResult($0) })
        }
    }

    func trending(completion: @escaping ([WebResult]?) -> Void) {
        engine.run("trending", ["limit": 30], slow: true) { result in
            if result["error"] != nil {
                completion(nil)
                return
            }
            completion((result["results"] as? [[String: Any]] ?? []).map { WebResult($0) })
        }
    }

    func openSearch(_ query: String) {
        pendingSearch = query
        tab = .search
    }

    // MARK: - Bibliothèque

    func isFavorite(_ track: Track) -> Bool {
        favorites.contains(track.path)
    }

    private func checkExists(_ track: Track) -> Bool {
        if FileManager.default.fileExists(atPath: track.path) { return true }
        show(L("Fichier introuvable (supprimé ou déplacé)."), .warn)
        return false
    }

    func toggleFavorite(_ track: Track) {
        guard checkExists(track) else { return }
        engine.run("toggle_favorite", ["entry": track.raw]) { [weak self] result in
            guard let self = self else { return }
            if jsonBool(result["favorite"]) {
                self.show(L("Ajouté aux favoris."), .ok)
            } else if result["error"] == nil {
                self.show(L("Retiré des favoris."), .info)
            }
            UISelectionFeedbackGenerator().selectionChanged()
            self.refreshLibrary()
        }
    }

    func add(_ track: Track, to playlist: Playlist) {
        guard checkExists(track) else { return }
        engine.run("playlist_add", ["uid": playlist.id, "entry": track.raw]) { [weak self] result in
            guard let self = self else { return }
            if jsonBool(result["added"]) {
                self.show(L("Ajouté à « {name} ».", ["name": playlist.name]), .ok)
            } else {
                self.show(L("Déjà dans « {name} ».", ["name": playlist.name]), .info)
            }
            self.refreshLibrary()
        }
    }

    func createPlaylist(_ name: String, adding track: Track? = nil, completion: ((String) -> Void)? = nil) {
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalName = clean.isEmpty ? L("Ma playlist") : clean
        engine.run("playlist_create", ["name": finalName]) { [weak self] result in
            guard let self = self, let uid = jsonString(result["uid"]) else { return }
            if let track = track {
                self.engine.run("playlist_add", ["uid": uid, "entry": track.raw]) { _ in
                    self.show(L("Ajouté à « {name} ».", ["name": finalName]), .ok)
                    self.refreshLibrary()
                }
            } else {
                self.show(L("« {name} » créée.", ["name": finalName]), .ok)
                self.refreshLibrary()
            }
            completion?(uid)
        }
    }

    func renamePlaylist(_ playlist: Playlist, to name: String) {
        engine.run("playlist_rename", ["uid": playlist.id, "name": name]) { [weak self] _ in
            self?.refreshLibrary()
        }
    }

    func deletePlaylist(_ playlist: Playlist) {
        engine.run("playlist_delete", ["uid": playlist.id]) { [weak self] _ in
            self?.show(L("« {name} » supprimée.", ["name": playlist.name]), .info)
            self?.refreshLibrary()
        }
    }

    func remove(_ track: Track, from playlist: Playlist) {
        engine.run("playlist_remove", ["uid": playlist.id, "filepath": track.path]) { [weak self] _ in
            self?.refreshLibrary()
        }
    }

    func delete(_ track: Track) {
        player?.forget(path: track.path)
        engine.run("delete", ["entry": track.raw]) { [weak self] _ in
            self?.show(L("Supprimé."), .info)
            self?.refreshLibrary()
        }
    }

    func playlist(_ id: String) -> Playlist? {
        playlists.first { $0.id == id }
    }

    // MARK: - Coffre

    func authenticate(_ reason: String, completion: @escaping (Bool) -> Void) {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            // Aucun code ni Face ID configuré sur l'iPhone : rien à protéger.
            completion(true)
            return
        }
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { success, _ in
            DispatchQueue.main.async { completion(success) }
        }
    }

    func openVault() {
        unlockVault { [weak self] in
            guard let self = self else { return }
            self.tab = .playlists
            self.playlistPath = [.vault]
        }
    }

    func unlockVault(then action: @escaping () -> Void) {
        if vaultUnlocked {
            action()
            return
        }
        authenticate(L("Ouvrir le Coffre d'ADA'S")) { [weak self] ok in
            guard let self = self else { return }
            if ok {
                self.vaultUnlocked = true
                action()
            } else {
                self.show(L("Coffre verrouillé : identification refusée."), .warn)
            }
        }
    }

    func lockVault() {
        vaultUnlocked = false
    }

    func moveToVault(_ track: Track) {
        guard checkExists(track) else { return }
        player?.forget(path: track.path)
        engine.run("vault_add", ["entry": track.raw]) { [weak self] result in
            guard let self = self else { return }
            if let error = jsonString(result["error"]) {
                self.show(error == "already" ? L("Déjà dans le Coffre.") : L("Impossible de ranger ce fichier dans le Coffre."),
                          .warn)
            } else {
                self.show(L("Rangé dans le Coffre."), .ok)
            }
            self.refreshLibrary()
        }
    }

    func removeFromVault(_ track: Track) {
        player?.forget(path: track.path)
        engine.run("vault_remove", ["item": track.raw]) { [weak self] result in
            guard let self = self else { return }
            if result["error"] != nil {
                self.show(L("Impossible de sortir ce fichier du Coffre."), .error)
            } else {
                self.show(L("Sorti du Coffre : de retour dans la bibliothèque."), .info)
            }
            self.refreshLibrary()
        }
    }

    // MARK: - Verrouillage de l'app

    func unlockApp() {
        authenticate(L("Déverrouiller ADA'S")) { [weak self] ok in
            if ok {
                withAnimation { self?.appLocked = false }
            }
        }
    }

    func scenePhaseChanged(_ phase: ScenePhase) {
        switch phase {
        case .background:
            lockVault()
            if UserDefaults.standard.bool(forKey: "appLock") {
                appLocked = true
            }
            beginBackgroundWork()
        case .active:
            endBackgroundWork()
            if appLocked {
                unlockApp()
            }
            poll()
        default:
            break
        }
    }

    /// Laisse quelques minutes aux imports en cours quand l'app passe en arrière-plan.
    private func beginBackgroundWork() {
        guard backgroundTask == .invalid, jobs.contains(where: { $0.active }) else { return }
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "ADA imports") { [weak self] in
            self?.endBackgroundWork()
        }
    }

    private func endBackgroundWork() {
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
    }

    // MARK: - Compression (AAC 96 kbit/s)

    func compress(_ track: Track) {
        guard checkExists(track) else { return }
        guard !compressing.contains(track.path) else {
            show(L("Compression déjà en cours pour ce morceau."), .info)
            return
        }
        let source = track.fileURL
        let target = source.deletingPathExtension().appendingPathExtension("compression.m4a")
        try? FileManager.default.removeItem(at: target)
        let before = fileSize(track.path)
        compressing.insert(track.path)
        show(L("Compression de « {title} »…", ["title": track.title]), .info)
        AudioCompressor.compress(source: source, target: target, bitrate: 96_000) { [weak self] ok in
            guard let self = self else { return }
            self.compressing.remove(track.path)
            let after = fileSize(target.path)
            guard ok, after > 0 else {
                try? FileManager.default.removeItem(at: target)
                self.show(L("Compression impossible pour ce fichier."), .error)
                return
            }
            guard after < before * 0.95 else {
                try? FileManager.default.removeItem(at: target)
                self.show(L("Ce morceau est déjà léger : rien à gagner."), .info)
                return
            }
            self.player?.forget(path: track.path)
            do {
                _ = try FileManager.default.replaceItemAt(source, withItemAt: target)
            } catch {
                try? FileManager.default.removeItem(at: target)
                self.show(L("Compression impossible (stockage)."), .error)
                return
            }
            self.engine.run("filesize", ["filepath": track.path, "size": after]) { _ in
                self.refreshLibrary()
            }
            self.show(L("Compressé : {size} (−{saved}).", ["size": Fmt.size(after), "saved": Fmt.size(before - after)]),
                      .ok)
        }
    }

    // MARK: - Réglages du moteur et sauvegarde

    func applyEngineSettings() {
        let defaults = UserDefaults.standard
        let values: [String: Any] = [
            "max_parallel": defaults.object(forKey: "maxParallel") as? Int ?? 2,
            "skip_duplicates": defaults.object(forKey: "skipDuplicates") as? Bool ?? true,
            "auto_retry": defaults.object(forKey: "autoRetry") as? Bool ?? true,
        ]
        engine.run("settings", ["values": values])
    }

    func backup() {
        engine.run("backup") { [weak self] result in
            if jsonBool(result["ok"]) {
                self?.show(L("Sauvegarde enregistrée dans l'app Fichiers › ADA'S."), .ok)
            } else {
                self?.show(L("Sauvegarde impossible (stockage indisponible)."), .error)
            }
        }
    }

    func restore() {
        engine.run("restore") { [weak self] result in
            guard let self = self else { return }
            if jsonBool(result["ok"]) {
                self.lockVault()
                self.show(L("Sauvegarde restaurée."), .ok)
                self.refreshLibrary()
            } else {
                self.show(L("Aucune sauvegarde trouvée dans l'app Fichiers › ADA'S."), .warn)
            }
        }
    }

    func storageText() -> String {
        var used: Double = 0
        let root = PythonEngine.documentsURL
        if let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.fileSizeKey]) {
            for case let url as URL in walker {
                let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
                used += Double(size)
            }
        }
        var text = L("Musique : {used}", ["used": Fmt.size(used).isEmpty ? "0" : Fmt.size(used)])
        if let values = try? root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
           let free = values.volumeAvailableCapacityForImportantUsage {
            text += " · " + L("Libre : {free}", ["free": Fmt.size(Double(free))])
        }
        return text
    }
}

func fileSize(_ path: String) -> Double {
    guard let attributes = try? FileManager.default.attributesOfItem(atPath: path),
          let size = attributes[.size] as? NSNumber else { return 0 }
    return size.doubleValue
}

/// Réencode un morceau en AAC (plus léger) avec AVFoundation.
enum AudioCompressor {
    static func compress(source: URL, target: URL, bitrate: Int, completion: @escaping (Bool) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let finish: (Bool) -> Void = { ok in
                DispatchQueue.main.async { completion(ok) }
            }
            let asset = AVURLAsset(url: source)
            guard let track = asset.tracks(withMediaType: .audio).first,
                  let reader = try? AVAssetReader(asset: asset),
                  let writer = try? AVAssetWriter(outputURL: target, fileType: .m4a) else {
                finish(false)
                return
            }
            let pcm: [String: Any] = [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMIsBigEndianKey: false,
                AVLinearPCMIsNonInterleaved: false,
                AVSampleRateKey: 44_100,
                AVNumberOfChannelsKey: 2,
            ]
            let output = AVAssetReaderTrackOutput(track: track, outputSettings: pcm)
            output.alwaysCopiesSampleData = false
            guard reader.canAdd(output) else {
                finish(false)
                return
            }
            reader.add(output)
            let aac: [String: Any] = [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 44_100,
                AVNumberOfChannelsKey: 2,
                AVEncoderBitRateKey: bitrate,
            ]
            let input = AVAssetWriterInput(mediaType: .audio, outputSettings: aac)
            input.expectsMediaDataInRealTime = false
            guard writer.canAdd(input) else {
                finish(false)
                return
            }
            writer.add(input)
            guard reader.startReading(), writer.startWriting() else {
                finish(false)
                return
            }
            writer.startSession(atSourceTime: .zero)
            let queue = DispatchQueue(label: "ada.compress")
            input.requestMediaDataWhenReady(on: queue) {
                while input.isReadyForMoreMediaData {
                    if let buffer = output.copyNextSampleBuffer() {
                        if !input.append(buffer) {
                            input.markAsFinished()
                            reader.cancelReading()
                            writer.cancelWriting()
                            finish(false)
                            return
                        }
                    } else {
                        input.markAsFinished()
                        let readOK = reader.status == .completed
                        writer.finishWriting {
                            finish(readOK && writer.status == .completed)
                        }
                        return
                    }
                }
            }
        }
    }
}
