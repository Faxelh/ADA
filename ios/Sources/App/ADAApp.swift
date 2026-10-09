import SwiftUI

@main
struct ADAApp: App {
    @StateObject private var model = AppModel()
    @StateObject private var player = AudioPlayer()
    @AppStorage("theme") private var theme = "system"
    @AppStorage("language") private var language = "system"
    @Environment(\.scenePhase) private var scenePhase

    init() {
        SelfTest.runIfRequested()
    }

    private var scheme: ColorScheme? {
        switch theme {
        case "light": return .light
        case "dark": return .dark
        default: return nil
        }
    }

    var body: some Scene {
        WindowGroup {
            let _ = Lang.apply(language)
            RootView()
                .environmentObject(model)
                .environmentObject(player)
                .tint(.ada)
                .preferredColorScheme(scheme)
                .id(language)
                .onAppear {
                    model.attach(player)
                }
                .onChange(of: scenePhase) { phase in
                    model.scenePhaseChanged(phase)
                }
                .onChange(of: language) { _ in
                    model.setEngineLanguage()
                }
        }
    }
}

struct RootView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var player: AudioPlayer
    @State private var showPlayer = false

    var body: some View {
        TabView(selection: $model.tab) {
            HomeView()
                .safeAreaInset(edge: .bottom) { MiniPlayer { showPlayer = true } }
                .tabItem { Label(L("Accueil"), systemImage: "house.fill") }
                .tag(AppTab.home)
            SearchView()
                .safeAreaInset(edge: .bottom) { MiniPlayer { showPlayer = true } }
                .tabItem { Label(L("Rechercher"), systemImage: "magnifyingglass") }
                .tag(AppTab.search)
            PlaylistsView()
                .safeAreaInset(edge: .bottom) { MiniPlayer { showPlayer = true } }
                .tabItem { Label(L("Playlists"), systemImage: "music.note.list") }
                .tag(AppTab.playlists)
            TrendingView()
                .safeAreaInset(edge: .bottom) { MiniPlayer { showPlayer = true } }
                .tabItem { Label(L("Tendances"), systemImage: "chart.line.uptrend.xyaxis") }
                .tag(AppTab.trending)
            SettingsView()
                .safeAreaInset(edge: .bottom) { MiniPlayer { showPlayer = true } }
                .tabItem { Label(L("Réglages"), systemImage: "gearshape.fill") }
                .tag(AppTab.settings)
        }
        .overlay(alignment: .top) {
            if let toast = model.toast {
                ToastBanner(toast: toast)
                    .padding(.top, 4)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .onTapGesture {
                        withAnimation { model.toast = nil }
                    }
            }
        }
        .overlay {
            if model.appLocked {
                LockView()
                    .transition(.opacity)
            }
        }
        .sheet(isPresented: $showPlayer) {
            NowPlayingView()
                .environmentObject(player)
                .environmentObject(model)
        }
        .onChange(of: model.tab) { tab in
            if tab != .playlists {
                model.lockVault()
            }
        }
    }
}
