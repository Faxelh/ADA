import AVKit
import SwiftUI

extension Color {
    /// Couleur d'accent d'ADA'S (rose-rouge #FF375F).
    static let ada = Color(red: 1.0, green: 55.0 / 255.0, blue: 95.0 / 255.0)
}

// MARK: - Pochettes

private let artworkPalettes: [[Color]] = [
    [Color(red: 1.0, green: 0.22, blue: 0.37), Color(red: 0.55, green: 0.12, blue: 0.45)],
    [Color(red: 0.98, green: 0.55, blue: 0.20), Color(red: 0.85, green: 0.20, blue: 0.30)],
    [Color(red: 0.35, green: 0.35, blue: 0.95), Color(red: 0.62, green: 0.25, blue: 0.85)],
    [Color(red: 0.10, green: 0.70, blue: 0.65), Color(red: 0.15, green: 0.35, blue: 0.70)],
    [Color(red: 0.95, green: 0.35, blue: 0.60), Color(red: 0.98, green: 0.65, blue: 0.35)],
    [Color(red: 0.30, green: 0.75, blue: 0.40), Color(red: 0.10, green: 0.45, blue: 0.40)],
]

func artworkColors(_ seed: String) -> [Color] {
    var hash: UInt32 = 5381
    for byte in seed.utf8 {
        hash = (hash &* 33) &+ UInt32(byte)
    }
    return artworkPalettes[Int(hash % UInt32(artworkPalettes.count))]
}

/// Miniature d'un morceau : l'image en ligne si elle existe, sinon un dégradé.
struct Artwork: View {
    let url: String?
    let seed: String
    var size: CGFloat = 52
    var radius: CGFloat = 8

    var body: some View {
        ZStack {
            LinearGradient(colors: artworkColors(seed), startPoint: .topLeading, endPoint: .bottomTrailing)
            Image(systemName: "music.note")
                .font(.system(size: size * 0.38, weight: .semibold))
                .foregroundColor(.white.opacity(0.9))
            if let link = url, let address = URL(string: link) {
                AsyncImage(url: address) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFill()
                    } else {
                        Color.clear
                    }
                }
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
    }
}

/// Petit indicateur « en cours de lecture » (barres animées).
struct PlayingBars: View {
    var playing: Bool
    @State private var animate = false
    private static let high: [CGFloat] = [12, 6, 10]
    private static let low: [CGFloat] = [5, 9, 4]

    var body: some View {
        HStack(alignment: .bottom, spacing: 2) {
            ForEach(0..<3, id: \.self) { index in
                RoundedRectangle(cornerRadius: 1)
                    .fill(Color.ada)
                    .frame(width: 3, height: animate && playing ? PlayingBars.high[index] : PlayingBars.low[index])
            }
        }
        .frame(width: 16, height: 14, alignment: .bottom)
        .animation(playing ? .easeInOut(duration: 0.45).repeatForever(autoreverses: true) : .default, value: animate)
        .onAppear { animate = true }
    }
}

// MARK: - Lignes

/// Ligne d'un morceau : pochette, titre, « 3:17 · 5,1 Mo », menu ⋯.
struct TrackRow<MenuContent: View>: View {
    @EnvironmentObject var player: AudioPlayer
    let track: Track
    var rank: Int? = nil
    let onTap: () -> Void
    @ViewBuilder var menu: () -> MenuContent

    var body: some View {
        HStack(spacing: 12) {
            if let rank = rank {
                Text("\(rank)")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundColor(.secondary)
                    .frame(width: 22)
            }
            Artwork(url: track.thumbnail, seed: track.id)
            VStack(alignment: .leading, spacing: 3) {
                Text(track.title)
                    .font(.system(size: 16, weight: .semibold))
                    .lineLimit(2)
                    .foregroundColor(track.exists ? .primary : .secondary)
                Text(track.exists ? track.meta : L("Supprimé ou déplacé"))
                    .font(.system(size: 13))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            if player.current?.path == track.path {
                PlayingBars(playing: player.isPlaying)
            }
            Menu {
                menu()
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundColor(.secondary)
                    .frame(width: 34, height: 40)
                    .contentShape(Rectangle())
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .onTapGesture(perform: onTap)
    }
}

/// Actions d'un morceau de la bibliothèque (menu ⋯ et appui long).
struct TrackActions: View {
    @EnvironmentObject var model: AppModel
    let track: Track
    @Binding var picking: Track?
    @Binding var deleting: Track?

    var body: some View {
        Button {
            model.toggleFavorite(track)
        } label: {
            if model.isFavorite(track) {
                Label(L("Retirer des favoris"), systemImage: "heart.slash")
            } else {
                Label(L("Favori"), systemImage: "heart")
            }
        }
        Button {
            picking = track
        } label: {
            Label(L("Ajouter à une playlist"), systemImage: "text.badge.plus")
        }
        if track.exists {
            ShareLink(item: track.fileURL) {
                Label(L("Partager"), systemImage: "square.and.arrow.up")
            }
        }
        Button {
            model.compress(track)
        } label: {
            Label(L("Compresser"), systemImage: "arrow.down.right.and.arrow.up.left")
        }
        Button {
            model.moveToVault(track)
        } label: {
            Label(L("Mettre au Coffre"), systemImage: "lock")
        }
        Divider()
        Button(role: .destructive) {
            deleting = track
        } label: {
            Label(L("Supprimer"), systemImage: "trash")
        }
    }
}

/// Ligne d'un import en cours.
struct JobRow: View {
    @EnvironmentObject var model: AppModel
    let job: Job

    var body: some View {
        HStack(spacing: 12) {
            Artwork(url: job.thumbnail, seed: job.url)
            VStack(alignment: .leading, spacing: 5) {
                Text(job.title)
                    .font(.system(size: 15, weight: .semibold))
                    .lineLimit(1)
                if job.active || job.isPaused {
                    ProgressView(value: max(0, min(1, job.fraction)))
                        .tint(.ada)
                }
                Text(job.statusText)
                    .font(.system(size: 12))
                    .foregroundColor(job.isFailed ? .red : .secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 4)
            if job.isFailed {
                Button {
                    model.jobAction("retry", job)
                } label: {
                    Image(systemName: "arrow.clockwise.circle.fill")
                        .font(.system(size: 26))
                        .foregroundColor(.ada)
                }
                .buttonStyle(.borderless)
            } else if job.isPaused {
                Button {
                    model.jobAction("resume", job)
                } label: {
                    Image(systemName: "play.circle.fill")
                        .font(.system(size: 26))
                        .foregroundColor(.ada)
                }
                .buttonStyle(.borderless)
            }
            Button {
                model.jobAction("cancel", job)
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 24))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.borderless)
        }
        .padding(.vertical, 4)
    }
}

/// Ligne d'un résultat de recherche ou des tendances, avec bouton d'import.
struct WebResultRow: View {
    @EnvironmentObject var model: AppModel
    let result: WebResult
    var rank: Int? = nil

    var body: some View {
        HStack(spacing: 12) {
            if let rank = rank {
                Text("\(rank)")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundColor(rank <= 3 ? .ada : .secondary)
                    .frame(width: 24)
            }
            Artwork(url: result.thumbnail, seed: result.url, size: 56)
            VStack(alignment: .leading, spacing: 3) {
                Text(result.title)
                    .font(.system(size: 15, weight: .semibold))
                    .lineLimit(2)
                Text(result.meta)
                    .font(.system(size: 13))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            Button {
                model.importResult(result)
            } label: {
                Image(systemName: "arrow.down.circle.fill")
                    .font(.system(size: 28))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundColor(.ada)
            }
            .buttonStyle(.borderless)
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Tuiles, boutons, états

/// Grande tuile arrondie (« Coller un lien », « Coffre »).
struct Tile: View {
    let icon: String
    let title: String
    var tint: Color = .ada
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 10) {
                Image(systemName: icon)
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundColor(tint)
                HStack(spacing: 3) {
                    Text(title)
                        .font(.system(size: 15, weight: .semibold))
                    Image(systemName: "chevron.right")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.secondary)
                }
                .foregroundColor(.primary)
            }
            .frame(maxWidth: .infinity)
            .frame(height: 104)
            .background(
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .fill(tint.opacity(0.13))
            )
        }
        .buttonStyle(.plain)
    }
}

/// Bouton rond de la barre du haut (« + », « ⋯ »), comme dans Elara.
struct CircleIcon: View {
    let systemName: String

    var body: some View {
        Image(systemName: systemName)
            .font(.system(size: 15, weight: .bold))
            .foregroundColor(.ada)
            .frame(width: 32, height: 32)
            .background(Circle().fill(Color(.tertiarySystemFill)))
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    var filled = true

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 16, weight: .semibold))
            .foregroundColor(filled ? .white : .ada)
            .frame(maxWidth: .infinity)
            .frame(height: 48)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(filled ? Color.ada : Color(.tertiarySystemFill))
            )
            .opacity(configuration.isPressed ? 0.75 : 1)
    }
}

struct EmptyState: View {
    let icon: String
    let title: String
    var subtitle: String = ""

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 40, weight: .regular))
                .foregroundColor(.secondary)
            Text(title)
                .font(.system(size: 17, weight: .semibold))
                .multilineTextAlignment(.center)
            if !subtitle.isEmpty {
                Text(subtitle)
                    .font(.system(size: 14))
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
        .padding(.horizontal, 24)
    }
}

/// Icône colorée des Réglages (carré arrondi, comme iOS).
struct SettingsIcon: View {
    let systemName: String
    let color: Color

    var body: some View {
        Image(systemName: systemName)
            .font(.system(size: 14, weight: .semibold))
            .foregroundColor(.white)
            .frame(width: 29, height: 29)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(color))
    }
}

struct SettingsLabel: View {
    let title: String
    let icon: String
    let color: Color

    var body: some View {
        Label {
            Text(title)
        } icon: {
            SettingsIcon(systemName: icon, color: color)
        }
    }
}

/// Bandeau de message en haut de l'écran.
struct ToastBanner: View {
    let toast: Toast

    private var icon: (String, Color) {
        switch toast.kind {
        case .ok: return ("checkmark.circle.fill", .green)
        case .warn: return ("exclamationmark.triangle.fill", .orange)
        case .error: return ("xmark.octagon.fill", .red)
        case .info: return ("info.circle.fill", .ada)
        }
    }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon.0)
                .foregroundColor(icon.1)
                .font(.system(size: 18))
            Text(toast.text)
                .font(.system(size: 15, weight: .medium))
                .foregroundColor(.primary)
                .lineLimit(3)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(.regularMaterial)
                .shadow(color: .black.opacity(0.18), radius: 14, y: 6)
        )
        .padding(.horizontal, 14)
    }
}

/// Bouton AirPlay natif (choix de l'enceinte ou de l'Apple TV).
struct AirPlayButton: UIViewRepresentable {
    var tint: UIColor = .label

    func makeUIView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView()
        view.tintColor = tint
        view.activeTintColor = UIColor(red: 1.0, green: 55.0 / 255.0, blue: 95.0 / 255.0, alpha: 1)
        view.prioritizesVideoDevices = false
        return view
    }

    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {
        uiView.tintColor = tint
    }
}

// MARK: - Choix d'une playlist

struct PlaylistPicker: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let track: Track
    @State private var newName = ""

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: 12) {
                        Artwork(url: track.thumbnail, seed: track.id, size: 44)
                        Text(track.title)
                            .font(.system(size: 15, weight: .semibold))
                            .lineLimit(2)
                    }
                }
                if !model.playlists.isEmpty {
                    Section(L("Mes playlists")) {
                        ForEach(model.playlists) { playlist in
                            Button {
                                model.add(track, to: playlist)
                                dismiss()
                            } label: {
                                HStack {
                                    Image(systemName: "music.note.list")
                                        .foregroundColor(.ada)
                                        .frame(width: 28)
                                    Text(playlist.name)
                                        .foregroundColor(.primary)
                                    Spacer()
                                    Text("\(playlist.items.count)")
                                        .foregroundColor(.secondary)
                                }
                            }
                        }
                    }
                }
                Section(L("Nouvelle playlist")) {
                    HStack {
                        TextField(L("Nom de la nouvelle playlist"), text: $newName)
                            .submitLabel(.done)
                            .onSubmit(create)
                        Button(L("Créer"), action: create)
                            .font(.system(size: 16, weight: .semibold))
                    }
                }
            }
            .navigationTitle(L("Ajouter à une playlist"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(L("Annuler")) { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func create() {
        model.createPlaylist(newName, adding: track)
        dismiss()
    }
}

/// Modificateur commun : choix de playlist et confirmation de suppression.
struct TrackSheets: ViewModifier {
    @EnvironmentObject var model: AppModel
    @Binding var picking: Track?
    @Binding var deleting: Track?

    func body(content: Content) -> some View {
        content
            .sheet(item: $picking) { track in
                PlaylistPicker(track: track)
                    .environmentObject(model)
            }
            .confirmationDialog(
                L("Supprimer ce morceau ?"),
                isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                titleVisibility: .visible,
                presenting: deleting
            ) { track in
                Button(L("Supprimer"), role: .destructive) {
                    model.delete(track)
                    deleting = nil
                }
                Button(L("Annuler"), role: .cancel) {
                    deleting = nil
                }
            } message: { track in
                Text(L("« {title} » sera supprimé de l'iPhone.", ["title": track.title]))
            }
    }
}

extension View {
    func trackSheets(picking: Binding<Track?>, deleting: Binding<Track?>) -> some View {
        modifier(TrackSheets(picking: picking, deleting: deleting))
    }
}
