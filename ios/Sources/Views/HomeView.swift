import SwiftUI

/// Onglet Accueil : tuiles, imports en cours, bibliothèque (Tout / Favoris / Récents).
struct HomeView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var player: AudioPlayer
    @AppStorage("homeFilter") private var filter = "all"
    @AppStorage("homeSort") private var sort = "date"
    @State private var picking: Track?
    @State private var deleting: Track?

    private var shown: [Track] {
        var list = model.tracks
        switch filter {
        case "favorites":
            list = list.filter { model.favorites.contains($0.path) }
        case "recent":
            let limit = Date().addingTimeInterval(-7 * 86_400)
            list = list.filter { ($0.addedDate ?? .distantPast) >= limit }
        default:
            break
        }
        switch sort {
        case "name":
            list.sort { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
        case "size":
            list.sort { ($0.filesize ?? 0) > ($1.filesize ?? 0) }
        default:
            break
        }
        return list
    }

    private var visibleJobs: [Job] {
        model.jobs.filter { $0.active || $0.isPaused || $0.isFailed }
    }

    var body: some View {
        NavigationStack {
            List {
                if let error = model.engineError {
                    Section {
                        EngineErrorCard(message: error)
                    }
                }

                Section {
                    HStack(spacing: 12) {
                        Tile(icon: "doc.on.clipboard.fill", title: L("Coller un lien")) {
                            model.importClipboard()
                        }
                        Tile(icon: "lock.fill", title: L("Coffre"), tint: .red) {
                            model.openVault()
                        }
                    }
                    .listRowInsets(EdgeInsets(top: 4, leading: 16, bottom: 8, trailing: 16))
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
                }

                if !visibleJobs.isEmpty {
                    Section {
                        ForEach(visibleJobs) { job in
                            JobRow(job: job)
                        }
                    } header: {
                        Text(L("Imports en cours"))
                    }
                }

                Section {
                    Picker("", selection: $filter) {
                        Text(L("Tout")).tag("all")
                        Text(L("Favoris")).tag("favorites")
                        Text(L("Récents")).tag("recent")
                    }
                    .pickerStyle(.segmented)
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)

                    let list = shown
                    if list.isEmpty {
                        emptyView
                            .listRowSeparator(.hidden)
                            .listRowBackground(Color.clear)
                    } else {
                        ForEach(Array(list.enumerated()), id: \.element.id) { index, track in
                            TrackRow(track: track, onTap: { player.play(list, start: index) }) {
                                TrackActions(track: track, picking: $picking, deleting: $deleting)
                            }
                            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                Button(role: .destructive) {
                                    deleting = track
                                } label: {
                                    Label(L("Supprimer"), systemImage: "trash")
                                }
                            }
                            .swipeActions(edge: .leading) {
                                Button {
                                    model.toggleFavorite(track)
                                } label: {
                                    Label(L("Favori"), systemImage: model.isFavorite(track) ? "heart.slash" : "heart")
                                }
                                .tint(.ada)
                            }
                            .contextMenu {
                                TrackActions(track: track, picking: $picking, deleting: $deleting)
                            }
                        }
                    }
                }
            }
            .listStyle(.plain)
            .navigationTitle("ADA'S")
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        model.importClipboard()
                    } label: {
                        CircleIcon(systemName: "plus")
                    }
                    .accessibilityLabel(L("Coller un lien"))
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Menu {
                        Picker(L("Trier par"), selection: $sort) {
                            Label(L("Date d'ajout"), systemImage: "calendar").tag("date")
                            Label(L("Nom"), systemImage: "textformat").tag("name")
                            Label(L("Taille (plus gros)"), systemImage: "externaldrive").tag("size")
                        }
                        if !visibleJobs.isEmpty {
                            Divider()
                            Button {
                                model.jobAction("pause_all")
                            } label: {
                                Label(L("Tout mettre en pause"), systemImage: "pause")
                            }
                            Button {
                                model.jobAction("resume_all")
                            } label: {
                                Label(L("Tout reprendre"), systemImage: "play")
                            }
                        }
                    } label: {
                        CircleIcon(systemName: "ellipsis")
                    }
                }
            }
            .refreshable {
                await MainActor.run { model.refreshLibrary() }
            }
            .trackSheets(picking: $picking, deleting: $deleting)
        }
    }

    @ViewBuilder
    private var emptyView: some View {
        if !model.libraryLoaded && model.engineError == nil {
            HStack {
                Spacer()
                ProgressView()
                Spacer()
            }
            .padding(.vertical, 40)
        } else if filter == "favorites" {
            EmptyState(icon: "heart", title: L("Aucun favori"),
                       subtitle: L("Touchez ⋯ puis « Favori » sur un morceau pour le retrouver ici."))
        } else {
            EmptyState(icon: "music.note", title: L("Vos musiques apparaîtront ici."),
                       subtitle: L("Copiez un lien YouTube puis touchez « Coller un lien », ou cherchez un titre dans l'onglet Rechercher."))
        }
    }
}

/// Le moteur Python n'a pas démarré : on affiche l'erreur exacte (une capture suffit pour corriger).
struct EngineErrorCard: View {
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(L("Le moteur d'import n'a pas démarré"), systemImage: "exclamationmark.triangle.fill")
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(.orange)
            Text(L("Faites une capture de cet écran et envoyez-la : elle indique exactement ce qui bloque."))
                .font(.system(size: 13))
                .foregroundColor(.secondary)
            Text(message)
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(.secondary)
                .textSelection(.enabled)
                .lineLimit(14)
        }
        .padding(.vertical, 6)
    }
}
