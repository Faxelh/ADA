import SwiftUI

struct Genre: Identifiable {
    let id: String
    let name: String
    let query: String
    let icon: String
    let colors: [Color]
}

let genres: [Genre] = [
    Genre(id: "afro", name: "Afrobeats", query: "afrobeats hits", icon: "sun.max.fill",
          colors: [Color(red: 0.98, green: 0.55, blue: 0.15), Color(red: 0.90, green: 0.25, blue: 0.20)]),
    Genre(id: "rapfr", name: "Rap FR", query: "rap français hits", icon: "mic.fill",
          colors: [Color(red: 0.30, green: 0.30, blue: 0.90), Color(red: 0.55, green: 0.20, blue: 0.80)]),
    Genre(id: "pop", name: "Pop", query: "pop hits", icon: "sparkles",
          colors: [Color(red: 1.0, green: 0.30, blue: 0.55), Color(red: 0.95, green: 0.55, blue: 0.70)]),
    Genre(id: "rnb", name: "R&B", query: "r&b hits", icon: "heart.fill",
          colors: [Color(red: 0.60, green: 0.15, blue: 0.45), Color(red: 0.95, green: 0.30, blue: 0.45)]),
    Genre(id: "zouk", name: "Zouk", query: "zouk", icon: "music.note",
          colors: [Color(red: 0.10, green: 0.65, blue: 0.70), Color(red: 0.10, green: 0.40, blue: 0.70)]),
    Genre(id: "coupe", name: "Coupé-décalé", query: "coupé décalé", icon: "figure.dance",
          colors: [Color(red: 0.95, green: 0.65, blue: 0.10), Color(red: 0.30, green: 0.70, blue: 0.30)]),
    Genre(id: "gospel", name: "Gospel", query: "gospel louange", icon: "hands.sparkles.fill",
          colors: [Color(red: 0.95, green: 0.75, blue: 0.30), Color(red: 0.85, green: 0.45, blue: 0.20)]),
    Genre(id: "dancehall", name: "Dancehall", query: "dancehall hits", icon: "speaker.wave.3.fill",
          colors: [Color(red: 0.20, green: 0.70, blue: 0.35), Color(red: 0.95, green: 0.80, blue: 0.20)]),
    Genre(id: "electro", name: "Électro", query: "electro hits", icon: "bolt.fill",
          colors: [Color(red: 0.15, green: 0.80, blue: 0.95), Color(red: 0.35, green: 0.25, blue: 0.90)]),
    Genre(id: "rumba", name: "Rumba", query: "rumba congolaise", icon: "guitars.fill",
          colors: [Color(red: 0.85, green: 0.30, blue: 0.25), Color(red: 0.55, green: 0.20, blue: 0.20)]),
    Genre(id: "jazz", name: "Jazz", query: "jazz", icon: "music.quarternote.3",
          colors: [Color(red: 0.25, green: 0.30, blue: 0.45), Color(red: 0.55, green: 0.45, blue: 0.30)]),
    Genre(id: "chill", name: "Chill", query: "chill lofi", icon: "moon.stars.fill",
          colors: [Color(red: 0.40, green: 0.35, blue: 0.75), Color(red: 0.20, green: 0.20, blue: 0.45)]),
]

/// Onglet Tendances : top des titres du moment et genres.
struct TrendingView: View {
    @EnvironmentObject var model: AppModel
    @State private var mode = "top"
    @State private var results: [WebResult] = []
    @State private var state = "idle"   // idle | loading | done | error

    private let columns = [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)]

    var body: some View {
        NavigationStack {
            List {
                Picker("", selection: $mode) {
                    Text(L("Top titres")).tag("top")
                    Text(L("Genres")).tag("genres")
                }
                .pickerStyle(.segmented)
                .listRowSeparator(.hidden)

                if mode == "top" {
                    topContent
                } else {
                    LazyVGrid(columns: columns, spacing: 12) {
                        ForEach(genres) { genre in
                            Button {
                                model.openSearch(genre.query)
                            } label: {
                                GenreTile(genre: genre)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 4, leading: 16, bottom: 16, trailing: 16))
                }
            }
            .listStyle(.plain)
            .navigationTitle(L("Tendances"))
            .refreshable {
                await MainActor.run { load(force: true) }
            }
            .onAppear {
                load(force: false)
            }
        }
    }

    @ViewBuilder
    private var topContent: some View {
        switch state {
        case "done":
            if results.isEmpty {
                EmptyState(icon: "chart.line.uptrend.xyaxis", title: L("Classement indisponible"),
                           subtitle: L("Vérifiez votre connexion, puis réessayez."))
                    .listRowSeparator(.hidden)
            } else {
                ForEach(Array(results.enumerated()), id: \.element.id) { index, result in
                    WebResultRow(result: result, rank: index + 1)
                }
            }
        case "error":
            VStack(spacing: 14) {
                EmptyState(icon: "wifi.exclamationmark", title: L("Classement indisponible"),
                           subtitle: L("Vérifiez votre connexion, puis réessayez."))
                Button(L("Réessayer")) {
                    load(force: true)
                }
                .buttonStyle(PrimaryButtonStyle())
                .padding(.horizontal, 60)
            }
            .listRowSeparator(.hidden)
        default:
            HStack(spacing: 10) {
                Spacer()
                ProgressView()
                Text(L("Chargement…"))
                    .foregroundColor(.secondary)
                Spacer()
            }
            .padding(.vertical, 40)
            .listRowSeparator(.hidden)
        }
    }

    private func load(force: Bool) {
        if !force && (state == "done" || state == "loading") { return }
        guard model.engineReady else {
            state = "loading"
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                if state == "loading" {
                    state = "idle"
                    load(force: false)
                }
            }
            return
        }
        state = "loading"
        model.trending { found in
            if let found = found {
                results = found
                state = "done"
            } else {
                state = "error"
            }
        }
    }
}

struct GenreTile: View {
    let genre: Genre

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            LinearGradient(colors: genre.colors, startPoint: .topLeading, endPoint: .bottomTrailing)
            Image(systemName: genre.icon)
                .font(.system(size: 38, weight: .semibold))
                .foregroundColor(.white.opacity(0.35))
                .rotationEffect(.degrees(-12))
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                .padding(12)
            Text(genre.name)
                .font(.system(size: 17, weight: .bold))
                .foregroundColor(.white)
                .padding(14)
        }
        .frame(height: 96)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}
