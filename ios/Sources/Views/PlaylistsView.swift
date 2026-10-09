import SwiftUI

/// Onglet Playlists : Favoris, Récents, Coffre (Face ID) et playlists perso.
struct PlaylistsView: View {
    @EnvironmentObject var model: AppModel
    @State private var creating = false
    @State private var newName = ""

    var body: some View {
        NavigationStack(path: $model.playlistPath) {
            List {
                Section {
                    NavigationLink(value: Route.favorites) {
                        row(L("Favoris"), icon: "heart.fill", color: .ada, count: model.favoriteTracks.count)
                    }
                    NavigationLink(value: Route.recent) {
                        row(L("Historique"), icon: "clock.fill", color: .orange, count: model.tracks.count)
                    }
                    Button {
                        model.openVault()
                    } label: {
                        HStack {
                            row(L("Coffre"), icon: "lock.fill", color: .red, count: nil)
                            Text("Face ID")
                                .foregroundColor(.secondary)
                            Image(systemName: "chevron.right")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(Color(.tertiaryLabel))
                        }
                    }
                    .foregroundColor(.primary)
                }

                Section {
                    ForEach(model.playlists) { playlist in
                        NavigationLink(value: Route.playlist(playlist.id)) {
                            HStack(spacing: 12) {
                                PlaylistCover(tracks: playlist.items, seed: playlist.id, size: 48)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(playlist.name)
                                        .font(.system(size: 16, weight: .semibold))
                                    Text(L("{count} morceau(x)", ["count": "\(playlist.items.count)"]))
                                        .font(.system(size: 13))
                                        .foregroundColor(.secondary)
                                }
                            }
                        }
                    }
                    .onDelete { offsets in
                        for index in offsets where model.playlists.indices.contains(index) {
                            model.deletePlaylist(model.playlists[index])
                        }
                    }
                    Button {
                        newName = ""
                        creating = true
                    } label: {
                        Label(L("Nouvelle playlist"), systemImage: "plus.circle.fill")
                            .foregroundColor(.ada)
                    }
                } header: {
                    Text(L("Mes playlists"))
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle(L("Playlists"))
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        newName = ""
                        creating = true
                    } label: {
                        CircleIcon(systemName: "plus")
                    }
                }
            }
            .navigationDestination(for: Route.self) { route in
                PlaylistDetailView(route: route)
            }
            .alert(L("Nouvelle playlist"), isPresented: $creating) {
                TextField(L("Nom de la playlist"), text: $newName)
                Button(L("Annuler"), role: .cancel) {}
                Button(L("Créer")) {
                    model.createPlaylist(newName) { uid in
                        model.playlistPath = [.playlist(uid)]
                    }
                }
            }
        }
    }

    private func row(_ title: String, icon: String, color: Color, count: Int?) -> some View {
        HStack(spacing: 12) {
            SettingsIcon(systemName: icon, color: color)
            Text(title)
            Spacer()
            if let count = count {
                Text("\(count)")
                    .foregroundColor(.secondary)
            }
        }
    }
}

/// Pochette d'une playlist : mosaïque des 4 premières pochettes.
struct PlaylistCover: View {
    let tracks: [Track]
    let seed: String
    var size: CGFloat = 48

    var body: some View {
        let first = Array(tracks.prefix(4))
        Group {
            if first.count >= 4 {
                VStack(spacing: 0) {
                    HStack(spacing: 0) {
                        Artwork(url: first[0].thumbnail, seed: first[0].id, size: size / 2, radius: 0)
                        Artwork(url: first[1].thumbnail, seed: first[1].id, size: size / 2, radius: 0)
                    }
                    HStack(spacing: 0) {
                        Artwork(url: first[2].thumbnail, seed: first[2].id, size: size / 2, radius: 0)
                        Artwork(url: first[3].thumbnail, seed: first[3].id, size: size / 2, radius: 0)
                    }
                }
            } else if let one = first.first {
                Artwork(url: one.thumbnail, seed: one.id, size: size, radius: 0)
            } else {
                ZStack {
                    LinearGradient(colors: artworkColors(seed), startPoint: .topLeading, endPoint: .bottomTrailing)
                    Image(systemName: "music.note.list")
                        .font(.system(size: size * 0.4, weight: .semibold))
                        .foregroundColor(.white)
                }
                .frame(width: size, height: size)
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.16, style: .continuous))
    }
}

/// Détail : Favoris, Historique, Coffre ou une playlist.
struct PlaylistDetailView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var player: AudioPlayer
    let route: Route
    @State private var picking: Track?
    @State private var deleting: Track?
    @State private var renaming = false
    @State private var newName = ""
    @State private var confirmDelete = false
    @Environment(\.dismiss) private var dismiss

    private var playlist: Playlist? {
        if case .playlist(let id) = route { return model.playlist(id) }
        return nil
    }

    private var title: String {
        switch route {
        case .favorites: return L("Favoris")
        case .recent: return L("Historique")
        case .vault: return L("Coffre")
        case .playlist: return playlist?.name ?? L("Playlist")
        }
    }

    private var tracks: [Track] {
        switch route {
        case .favorites: return model.favoriteTracks
        case .recent: return model.tracks
        case .vault: return model.vaultUnlocked ? model.vault : []
        case .playlist: return playlist?.items ?? []
        }
    }

    private var locked: Bool {
        if case .vault = route { return !model.vaultUnlocked }
        return false
    }

    var body: some View {
        List {
            if locked {
                VStack(spacing: 16) {
                    EmptyState(icon: "lock.fill", title: L("Coffre verrouillé"),
                               subtitle: L("Identifiez-vous avec Face ID pour voir vos morceaux privés."))
                    Button(L("Déverrouiller")) {
                        model.unlockVault {}
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .padding(.horizontal, 50)
                }
                .listRowSeparator(.hidden)
                .listRowBackground(Color.clear)
            } else {
                header
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 12, trailing: 16))
                if tracks.isEmpty {
                    EmptyState(icon: route == .vault ? "lock" : "music.note", title: L("Aucun morceau"),
                               subtitle: emptyText)
                        .listRowSeparator(.hidden)
                        .listRowBackground(Color.clear)
                } else {
                    let list = tracks
                    ForEach(Array(list.enumerated()), id: \.element.id) { index, track in
                        TrackRow(track: track, onTap: { player.play(list, start: index) }) {
                            menu(for: track)
                        }
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            if route != .recent {
                                Button(role: .destructive) {
                                    remove(track)
                                } label: {
                                    Label(removeLabel, systemImage: route == .vault ? "lock.open" : "minus.circle")
                                }
                            }
                        }
                    }
                }
            }
        }
        .listStyle(.plain)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                if let playlist = playlist {
                    Menu {
                        Button {
                            newName = playlist.name
                            renaming = true
                        } label: {
                            Label(L("Renommer"), systemImage: "pencil")
                        }
                        Button(role: .destructive) {
                            confirmDelete = true
                        } label: {
                            Label(L("Supprimer la playlist"), systemImage: "trash")
                        }
                    } label: {
                        CircleIcon(systemName: "ellipsis")
                    }
                }
            }
        }
        .alert(L("Renommer"), isPresented: $renaming) {
            TextField(L("Nom de la playlist"), text: $newName)
            Button(L("Annuler"), role: .cancel) {}
            Button(L("OK")) {
                if let playlist = playlist { model.renamePlaylist(playlist, to: newName) }
            }
        }
        .confirmationDialog(L("Supprimer la playlist ?"), isPresented: $confirmDelete, titleVisibility: .visible) {
            Button(L("Supprimer"), role: .destructive) {
                if let playlist = playlist {
                    model.deletePlaylist(playlist)
                    dismiss()
                }
            }
            Button(L("Annuler"), role: .cancel) {}
        } message: {
            Text(L("Les morceaux restent dans votre bibliothèque."))
        }
        .trackSheets(picking: $picking, deleting: $deleting)
        .onDisappear {
            if route == .vault { model.lockVault() }
        }
    }

    private var header: some View {
        VStack(spacing: 14) {
            HStack(spacing: 14) {
                PlaylistCover(tracks: tracks, seed: title, size: 96)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.system(size: 22, weight: .bold))
                        .lineLimit(2)
                    Text(L("{count} morceau(x)", ["count": "\(tracks.count)"]))
                        .font(.system(size: 14))
                        .foregroundColor(.secondary)
                    if route == .vault {
                        Label(L("Protégé par Face ID"), systemImage: "faceid")
                            .font(.system(size: 13))
                            .foregroundColor(.secondary)
                    }
                }
                Spacer()
            }
            if !tracks.isEmpty {
                HStack(spacing: 12) {
                    Button {
                        player.play(tracks, start: 0)
                    } label: {
                        Label(L("Tout lire"), systemImage: "play.fill")
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    Button {
                        let list = tracks
                        player.play(list, start: list.isEmpty ? 0 : Int.random(in: 0..<list.count), shuffled: true)
                    } label: {
                        Label(L("Aléatoire"), systemImage: "shuffle")
                    }
                    .buttonStyle(PrimaryButtonStyle(filled: false))
                }
            }
            if route == .vault {
                Text(L("Les morceaux du Coffre sont cachés de la bibliothèque et de l'app Fichiers. Le Coffre se reverrouille dès que vous le quittez."))
                    .font(.system(size: 13))
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.top, 6)
    }

    private var emptyText: String {
        switch route {
        case .vault: return L("Rangez ici vos morceaux privés : ⋯ puis « Mettre au Coffre » sur l'accueil.")
        case .favorites: return L("Touchez ⋯ puis « Favori » sur un morceau pour le retrouver ici.")
        default: return L("Ajoutez des morceaux depuis l'accueil : ⋯ puis « Ajouter à une playlist ».")
        }
    }

    private var removeLabel: String {
        route == .vault ? L("Sortir du Coffre") : L("Retirer")
    }

    @ViewBuilder
    private func menu(for track: Track) -> some View {
        if route == .recent {
            TrackActions(track: track, picking: $picking, deleting: $deleting)
        } else {
            if track.exists && route != .vault {
                ShareLink(item: track.fileURL) {
                    Label(L("Partager"), systemImage: "square.and.arrow.up")
                }
            }
            Button(role: route == .vault ? ButtonRole?.none : ButtonRole.destructive) {
                remove(track)
            } label: {
                Label(removeLabel, systemImage: route == .vault ? "lock.open" : "minus.circle")
            }
            if route == .vault {
                Button(role: .destructive) {
                    deleting = track
                } label: {
                    Label(L("Supprimer"), systemImage: "trash")
                }
            }
        }
    }

    private func remove(_ track: Track) {
        switch route {
        case .favorites:
            model.toggleFavorite(track)
        case .vault:
            model.removeFromVault(track)
        case .playlist:
            if let playlist = playlist { model.remove(track, from: playlist) }
        case .recent:
            break
        }
    }
}
