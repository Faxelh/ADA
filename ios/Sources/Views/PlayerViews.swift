import SwiftUI

/// Mini-lecteur posé au-dessus de la barre d'onglets.
struct MiniPlayer: View {
    @EnvironmentObject var player: AudioPlayer
    let open: () -> Void

    var body: some View {
        if let track = player.current {
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Artwork(url: track.thumbnail, seed: track.id, size: 42, radius: 7)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(track.title)
                            .font(.system(size: 15, weight: .semibold))
                            .lineLimit(1)
                        if !track.uploader.isEmpty {
                            Text(track.uploader)
                                .font(.system(size: 12))
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                    Spacer(minLength: 4)
                    Button {
                        player.toggle()
                    } label: {
                        Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                            .font(.system(size: 22))
                            .foregroundColor(.primary)
                            .frame(width: 40, height: 40)
                    }
                    .buttonStyle(.plain)
                    Button {
                        player.next()
                    } label: {
                        Image(systemName: "forward.fill")
                            .font(.system(size: 19))
                            .foregroundColor(.primary)
                            .frame(width: 36, height: 40)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                GeometryReader { geometry in
                    Rectangle()
                        .fill(Color.ada)
                        .frame(width: geometry.size.width * progress, height: 2)
                }
                .frame(height: 2)
            }
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(.regularMaterial)
                    .shadow(color: .black.opacity(0.15), radius: 10, y: 3)
            )
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .padding(.horizontal, 10)
            .padding(.bottom, 6)
            .contentShape(Rectangle())
            .onTapGesture(perform: open)
        }
    }

    private var progress: CGFloat {
        guard player.duration > 0 else { return 0 }
        return CGFloat(min(1, max(0, player.currentTime / player.duration)))
    }
}

/// Lecteur plein écran.
struct NowPlayingView: View {
    @EnvironmentObject var player: AudioPlayer
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var scrubbing = false
    @State private var scrubValue: Double = 0
    @State private var showQueue = false

    var body: some View {
        ZStack {
            background
            if let track = player.current {
                VStack(spacing: 0) {
                    Capsule()
                        .fill(Color.white.opacity(0.4))
                        .frame(width: 38, height: 5)
                        .padding(.top, 10)
                    HStack {
                        Button {
                            dismiss()
                        } label: {
                            Image(systemName: "chevron.down")
                                .font(.system(size: 18, weight: .semibold))
                                .frame(width: 40, height: 40)
                        }
                        Spacer()
                        Text(L("En cours de lecture"))
                            .font(.system(size: 13, weight: .semibold))
                            .opacity(0.8)
                        Spacer()
                        Button {
                            showQueue = true
                        } label: {
                            Image(systemName: "list.bullet")
                                .font(.system(size: 18, weight: .semibold))
                                .frame(width: 40, height: 40)
                        }
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 16)

                    Spacer(minLength: 12)
                    Artwork(url: track.thumbnail, seed: track.id, size: 300, radius: 22)
                        .shadow(color: .black.opacity(0.35), radius: 24, y: 12)
                        .scaleEffect(player.isPlaying ? 1 : 0.88)
                        .animation(.spring(response: 0.45, dampingFraction: 0.7), value: player.isPlaying)
                    Spacer(minLength: 12)

                    HStack(alignment: .center) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(track.title)
                                .font(.system(size: 21, weight: .bold))
                                .lineLimit(2)
                            if !track.uploader.isEmpty {
                                Text(track.uploader)
                                    .font(.system(size: 16))
                                    .opacity(0.7)
                                    .lineLimit(1)
                            }
                        }
                        Spacer()
                        Button {
                            model.toggleFavorite(track)
                        } label: {
                            Image(systemName: model.isFavorite(track) ? "heart.fill" : "heart")
                                .font(.system(size: 24))
                                .foregroundColor(model.isFavorite(track) ? .ada : .white)
                        }
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 28)

                    VStack(spacing: 4) {
                        Slider(value: Binding(
                            get: { scrubbing ? scrubValue : player.currentTime },
                            set: { scrubValue = $0 }
                        ), in: 0...max(player.duration, 1), onEditingChanged: { editing in
                            if editing {
                                scrubValue = player.currentTime
                                scrubbing = true
                            } else {
                                player.seek(to: scrubValue)
                                scrubbing = false
                            }
                        })
                        .tint(.white)
                        HStack {
                            Text(Fmt.clock(scrubbing ? scrubValue : player.currentTime))
                            Spacer()
                            Text("-" + Fmt.clock(max(0, player.duration - (scrubbing ? scrubValue : player.currentTime))))
                        }
                        .font(.system(size: 12, weight: .medium).monospacedDigit())
                        .foregroundColor(.white.opacity(0.7))
                    }
                    .padding(.horizontal, 28)
                    .padding(.top, 18)

                    HStack {
                        Button {
                            player.toggleShuffle()
                        } label: {
                            Image(systemName: "shuffle")
                                .font(.system(size: 20, weight: .semibold))
                                .foregroundColor(player.shuffle ? .ada : Color.white.opacity(0.8))
                        }
                        Spacer()
                        Button {
                            player.previous()
                        } label: {
                            Image(systemName: "backward.fill")
                                .font(.system(size: 30))
                        }
                        Spacer()
                        Button {
                            player.toggle()
                        } label: {
                            Image(systemName: player.isPlaying ? "pause.circle.fill" : "play.circle.fill")
                                .font(.system(size: 72))
                                .symbolRenderingMode(.hierarchical)
                        }
                        Spacer()
                        Button {
                            player.next()
                        } label: {
                            Image(systemName: "forward.fill")
                                .font(.system(size: 30))
                        }
                        Spacer()
                        Button {
                            player.cycleRepeat()
                        } label: {
                            Image(systemName: player.repeatMode == .one ? "repeat.1" : "repeat")
                                .font(.system(size: 20, weight: .semibold))
                                .foregroundColor(player.repeatMode == .off ? Color.white.opacity(0.8) : .ada)
                        }
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 30)
                    .padding(.top, 10)

                    HStack {
                        Menu {
                            ForEach([0, 15, 30, 45, 60], id: \.self) { minutes in
                                Button {
                                    player.setSleepTimer(minutes: minutes)
                                } label: {
                                    Text(minutes == 0 ? L("Désactivé") : L("{minutes} min", ["minutes": "\(minutes)"]))
                                }
                            }
                        } label: {
                            Image(systemName: player.sleepEnd == nil ? "moon.zzz" : "moon.zzz.fill")
                                .font(.system(size: 20))
                                .foregroundColor(player.sleepEnd == nil ? Color.white.opacity(0.8) : .ada)
                                .frame(width: 44, height: 44)
                        }
                        Spacer()
                        AirPlayButton(tint: .white)
                            .frame(width: 44, height: 44)
                        Spacer()
                        if track.exists {
                            ShareLink(item: track.fileURL) {
                                Image(systemName: "square.and.arrow.up")
                                    .font(.system(size: 20))
                                    .foregroundColor(.white.opacity(0.8))
                                    .frame(width: 44, height: 44)
                            }
                        } else {
                            Color.clear.frame(width: 44, height: 44)
                        }
                    }
                    .padding(.horizontal, 40)
                    .padding(.top, 14)
                    .padding(.bottom, 24)
                }
            } else {
                VStack(spacing: 16) {
                    EmptyState(icon: "music.note", title: L("Aucune lecture en cours"))
                    Button(L("Fermer")) { dismiss() }
                        .buttonStyle(PrimaryButtonStyle())
                        .padding(.horizontal, 80)
                }
                .foregroundColor(.white)
            }
        }
        .sheet(isPresented: $showQueue) {
            QueueSheet()
                .environmentObject(player)
        }
    }

    private var background: some View {
        let colors = artworkColors(player.current?.id ?? "ada")
        return ZStack {
            LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
            if let link = player.current?.thumbnail, let url = URL(string: link) {
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFill().blur(radius: 50).opacity(0.85)
                    } else {
                        Color.clear
                    }
                }
            }
            Color.black.opacity(0.35)
        }
        .ignoresSafeArea()
    }
}

/// File de lecture (« À suivre »).
struct QueueSheet: View {
    @EnvironmentObject var player: AudioPlayer
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                ForEach(Array(player.queue.enumerated()), id: \.offset) { offset, track in
                    HStack(spacing: 12) {
                        Artwork(url: track.thumbnail, seed: track.id, size: 44)
                        Text(track.title)
                            .font(.system(size: 15, weight: offset == player.index ? .bold : .regular))
                            .foregroundColor(offset == player.index ? .ada : .primary)
                            .lineLimit(2)
                        Spacer()
                        if offset == player.index {
                            PlayingBars(playing: player.isPlaying)
                        }
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        player.play(player.queue, start: offset, shuffled: false)
                    }
                }
            }
            .listStyle(.plain)
            .navigationTitle(L("À suivre"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(L("OK")) { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}

/// Écran de verrouillage (Face ID à l'ouverture).
struct LockView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ZStack {
            Color(.systemBackground).ignoresSafeArea()
            VStack(spacing: 16) {
                Spacer()
                Image("Logo")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 96, height: 96)
                    .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                Text("ADA'S")
                    .font(.system(size: 28, weight: .bold))
                Text(L("ADA'S est verrouillée."))
                    .foregroundColor(.secondary)
                Button {
                    model.unlockApp()
                } label: {
                    Label(L("Déverrouiller"), systemImage: "faceid")
                }
                .buttonStyle(PrimaryButtonStyle())
                .padding(.horizontal, 60)
                .padding(.top, 12)
                Spacer()
            }
        }
    }
}
