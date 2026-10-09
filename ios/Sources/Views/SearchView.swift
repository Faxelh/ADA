import SwiftUI

/// Onglet Rechercher : recherche YouTube, import en un geste, recherches récentes.
struct SearchView: View {
    @EnvironmentObject var model: AppModel
    @State private var query = ""
    @State private var results: [WebResult] = []
    @State private var state = "idle"   // idle | loading | done | error
    @State private var lastQuery = ""
    @State private var token = UUID()
    @AppStorage("recentSearches") private var recentStore = ""

    private var recent: [String] {
        recentStore.split(separator: "\n").map(String.init).filter { !$0.isEmpty }
    }

    var body: some View {
        NavigationStack {
            List {
                content
            }
            .listStyle(.plain)
            .navigationTitle(L("Rechercher"))
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always),
                        prompt: L("Titre, artiste, lien…"))
            .onSubmit(of: .search) {
                run(query)
            }
            .onChange(of: query) { value in
                if value.isEmpty {
                    state = "idle"
                    results = []
                }
            }
            .onAppear(perform: takePending)
            .onChange(of: model.pendingSearch) { _ in
                takePending()
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch state {
        case "loading":
            HStack(spacing: 10) {
                Spacer()
                ProgressView()
                Text(L("Recherche…"))
                    .foregroundColor(.secondary)
                Spacer()
            }
            .padding(.vertical, 40)
            .listRowSeparator(.hidden)
        case "error":
            VStack(spacing: 14) {
                EmptyState(icon: "wifi.exclamationmark", title: L("Recherche impossible"),
                           subtitle: L("Vérifiez votre connexion, puis réessayez."))
                Button(L("Réessayer")) {
                    run(lastQuery)
                }
                .buttonStyle(PrimaryButtonStyle())
                .padding(.horizontal, 60)
            }
            .listRowSeparator(.hidden)
        case "done":
            if results.isEmpty {
                EmptyState(icon: "magnifyingglass", title: L("Aucun résultat"),
                           subtitle: L("Essayez avec d'autres mots."))
                    .listRowSeparator(.hidden)
            } else {
                Section {
                    ForEach(results) { result in
                        WebResultRow(result: result)
                    }
                } header: {
                    Text(L("{count} résultat(s)", ["count": "\(results.count)"]))
                }
            }
        default:
            if recent.isEmpty {
                EmptyState(icon: "magnifyingglass", title: L("Cherchez un titre ou un artiste"),
                           subtitle: L("Vos recherches récentes apparaîtront ici."))
                    .listRowSeparator(.hidden)
            } else {
                Section {
                    ForEach(recent, id: \.self) { item in
                        Button {
                            query = item
                            run(item)
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: "clock.arrow.circlepath")
                                    .foregroundColor(.secondary)
                                Text(item)
                                    .foregroundColor(.primary)
                                Spacer()
                                Image(systemName: "arrow.up.left")
                                    .font(.system(size: 13))
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                } header: {
                    HStack {
                        Text(L("Récentes"))
                        Spacer()
                        Button(L("Effacer")) {
                            recentStore = ""
                        }
                        .font(.system(size: 14))
                        .textCase(nil)
                    }
                }
            }
        }
    }

    private func takePending() {
        guard let pending = model.pendingSearch else { return }
        model.pendingSearch = nil
        query = pending
        run(pending)
    }

    private func run(_ text: String) {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        // Un lien collé dans la recherche : on l'importe directement.
        if clean.hasPrefix("http://") || clean.hasPrefix("https://") {
            model.importText(clean)
            query = ""
            return
        }
        remember(clean)
        lastQuery = clean
        state = "loading"
        let current = UUID()
        token = current
        model.search(clean) { found in
            guard token == current else { return }
            if let found = found {
                results = found
                state = "done"
            } else {
                state = "error"
            }
        }
    }

    private func remember(_ text: String) {
        var list = recent.filter { $0.caseInsensitiveCompare(text) != .orderedSame }
        list.insert(text, at: 0)
        recentStore = list.prefix(12).joined(separator: "\n")
    }
}
