import AVFoundation
import MediaPlayer
import UIKit

/// Lecteur audio d'ADA'S : file de lecture, aléatoire, répétition,
/// lecture en arrière-plan, commandes de l'écran verrouillé et minuteur.
final class AudioPlayer: NSObject, ObservableObject {
    enum RepeatMode {
        case off, all, one
    }

    @Published private(set) var queue: [Track] = []
    @Published private(set) var index = 0
    @Published private(set) var isPlaying = false
    @Published private(set) var currentTime: Double = 0
    @Published private(set) var duration: Double = 0
    @Published var shuffle = false
    @Published var repeatMode: RepeatMode = .off
    @Published private(set) var sleepEnd: Date?

    var onMessage: ((String) -> Void)?

    private var player: AVPlayer?
    private var timeObserver: Any?
    private var endObserver: NSObjectProtocol?
    private var sleepTimer: Timer?
    private var artwork: MPMediaItemArtwork?
    private var artworkKey: String?
    private var original: [Track] = []
    private var isSeeking = false

    var current: Track? {
        queue.indices.contains(index) ? queue[index] : nil
    }

    override init() {
        super.init()
        configureRemoteCommands()
    }

    // MARK: - Lecture

    func play(_ tracks: [Track], start: Int = 0, shuffled: Bool = false) {
        let playable = tracks.filter { $0.exists }
        guard !playable.isEmpty else {
            onMessage?(L("Fichier introuvable (supprimé ou déplacé)."))
            return
        }
        var startTrack = tracks.indices.contains(start) ? tracks[start] : playable[0]
        if !startTrack.exists { startTrack = playable[0] }
        original = playable
        shuffle = shuffled
        if shuffled {
            var rest = playable.filter { $0.id != startTrack.id }
            rest.shuffle()
            queue = [startTrack] + rest
        } else {
            queue = playable
        }
        index = queue.firstIndex(where: { $0.id == startTrack.id }) ?? 0
        load(play: true)
    }

    func toggleShuffle() {
        guard let now = current else {
            shuffle.toggle()
            return
        }
        shuffle.toggle()
        if shuffle {
            var rest = original.filter { $0.id != now.id }
            rest.shuffle()
            queue = [now] + rest
            index = 0
        } else {
            queue = original
            index = queue.firstIndex(where: { $0.id == now.id }) ?? 0
        }
    }

    func cycleRepeat() {
        switch repeatMode {
        case .off: repeatMode = .all
        case .all: repeatMode = .one
        case .one: repeatMode = .off
        }
    }

    func toggle() {
        if isPlaying { pause() } else { resume() }
    }

    func resume() {
        guard let player = player else {
            if current != nil { load(play: true) }
            return
        }
        activateSession()
        player.play()
        isPlaying = true
        updateNowPlaying()
    }

    func pause() {
        player?.pause()
        isPlaying = false
        updateNowPlaying()
    }

    func next() {
        guard !queue.isEmpty else { return }
        if index + 1 < queue.count {
            index += 1
        } else if repeatMode == .off {
            seek(to: 0)
            pause()
            return
        } else {
            index = 0
        }
        load(play: true)
    }

    func previous() {
        if currentTime > 3 || index == 0 {
            seek(to: 0)
            return
        }
        index -= 1
        load(play: true)
    }

    func seek(to seconds: Double) {
        guard let player = player else { return }
        isSeeking = true
        player.seek(to: CMTime(seconds: max(0, seconds), preferredTimescale: 600)) { [weak self] _ in
            DispatchQueue.main.async {
                self?.isSeeking = false
                self?.updateNowPlaying()
            }
        }
        currentTime = max(0, seconds)
    }

    func stop() {
        player?.pause()
        if let observer = timeObserver {
            player?.removeTimeObserver(observer)
        }
        timeObserver = nil
        player = nil
        queue = []
        original = []
        index = 0
        isPlaying = false
        currentTime = 0
        duration = 0
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    /// Un fichier a été supprimé, déplacé ou compressé : on le retire de la file.
    func forget(path: String) {
        guard queue.contains(where: { $0.path == path }) else { return }
        if current?.path == path {
            stop()
            return
        }
        let now = current
        queue.removeAll { $0.path == path }
        original.removeAll { $0.path == path }
        index = queue.firstIndex(where: { $0.id == now?.id }) ?? 0
    }

    private func load(play: Bool) {
        guard let track = current else { return }
        guard FileManager.default.fileExists(atPath: track.path) else {
            onMessage?(L("Fichier introuvable (supprimé ou déplacé)."))
            if index + 1 < queue.count {
                index += 1
                load(play: play)
            }
            return
        }
        activateSession()
        let item = AVPlayerItem(url: track.fileURL)
        if player == nil {
            let newPlayer = AVPlayer()
            newPlayer.automaticallyWaitsToMinimizeStalling = false
            player = newPlayer
            timeObserver = newPlayer.addPeriodicTimeObserver(
                forInterval: CMTime(seconds: 0.5, preferredTimescale: 600), queue: .main
            ) { [weak self] time in
                self?.tick(time)
            }
        }
        if let observer = endObserver {
            NotificationCenter.default.removeObserver(observer)
        }
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main
        ) { [weak self] _ in
            self?.finished()
        }
        player?.replaceCurrentItem(with: item)
        currentTime = 0
        duration = track.duration ?? 0
        if play {
            player?.play()
            isPlaying = true
        }
        loadArtwork(for: track)
        updateNowPlaying()
    }

    private func tick(_ time: CMTime) {
        guard !isSeeking else { return }
        let seconds = time.seconds
        if seconds.isFinite { currentTime = seconds }
        if let itemDuration = player?.currentItem?.duration.seconds, itemDuration.isFinite, itemDuration > 0 {
            if abs(itemDuration - duration) > 0.5 {
                duration = itemDuration
                updateNowPlaying()
            }
        }
    }

    private func finished() {
        if repeatMode == .one {
            seek(to: 0)
            player?.play()
            return
        }
        next()
    }

    private func activateSession() {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .default, options: [])
        try? session.setActive(true)
    }

    // MARK: - Minuteur de sommeil

    func setSleepTimer(minutes: Int) {
        sleepTimer?.invalidate()
        sleepTimer = nil
        sleepEnd = nil
        guard minutes > 0 else { return }
        let end = Date().addingTimeInterval(Double(minutes) * 60)
        sleepEnd = end
        sleepTimer = Timer.scheduledTimer(withTimeInterval: Double(minutes) * 60, repeats: false) { [weak self] _ in
            self?.pause()
            self?.sleepEnd = nil
            self?.onMessage?(L("Minuteur de sommeil : lecture mise en pause."))
        }
    }

    // MARK: - Écran verrouillé et Centre de contrôle

    private func configureRemoteCommands() {
        let center = MPRemoteCommandCenter.shared()
        center.playCommand.addTarget { [weak self] _ in
            self?.resume()
            return .success
        }
        center.pauseCommand.addTarget { [weak self] _ in
            self?.pause()
            return .success
        }
        center.togglePlayPauseCommand.addTarget { [weak self] _ in
            self?.toggle()
            return .success
        }
        center.nextTrackCommand.addTarget { [weak self] _ in
            self?.next()
            return .success
        }
        center.previousTrackCommand.addTarget { [weak self] _ in
            self?.previous()
            return .success
        }
        center.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            self?.seek(to: event.positionTime)
            return .success
        }
    }

    private func updateNowPlaying() {
        guard let track = current else {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            return
        }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: track.title,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: currentTime,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0,
        ]
        if !track.uploader.isEmpty {
            info[MPMediaItemPropertyArtist] = track.uploader
        }
        if duration > 0 {
            info[MPMediaItemPropertyPlaybackDuration] = duration
        }
        if let artwork = artwork, artworkKey == track.id {
            info[MPMediaItemPropertyArtwork] = artwork
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func loadArtwork(for track: Track) {
        guard artworkKey != track.id else { return }
        artwork = nil
        artworkKey = nil
        guard let link = track.thumbnail, let url = URL(string: link) else { return }
        URLSession.shared.dataTask(with: url) { [weak self] data, _, _ in
            guard let data = data, let image = UIImage(data: data) else { return }
            DispatchQueue.main.async {
                guard let self = self, self.current?.id == track.id else { return }
                self.artwork = MPMediaItemArtwork(boundsSize: image.size) { _ in image }
                self.artworkKey = track.id
                self.updateNowPlaying()
            }
        }.resume()
    }
}
