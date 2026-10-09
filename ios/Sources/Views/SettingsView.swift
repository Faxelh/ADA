import SwiftUI

/// Onglet Réglages, organisé comme les Réglages d'iOS.
struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var player: AudioPlayer
    @AppStorage("language") private var language = "system"
    @AppStorage("theme") private var theme = "system"
    @AppStorage("keepAwake") private var keepAwake = true
    @AppStorage("appLock") private var appLock = false
    @AppStorage("maxParallel") private var maxParallel = 2
    @AppStorage("skipDuplicates") private var skipDuplicates = true
    @AppStorage("autoRetry") private var autoRetry = true
    @State private var sleepMinutes = 0
    @State private var storage = ""
    @State private var confirmRestore = false

    private let sleepChoices = [0, 5, 10, 15, 30, 45, 60]

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker(selection: $language) {
                        Text(L("Langue du téléphone")).tag("system")
                        Text("Français").tag("fr")
                        Text("English").tag("en")
                    } label: {
                        SettingsLabel(title: L("Langue"), icon: "globe", color: .blue)
                    }
                    .pickerStyle(.navigationLink)
                    Picker(selection: $theme) {
                        Text(L("Automatique (système)")).tag("system")
                        Text(L("Clair")).tag("light")
                        Text(L("Sombre")).tag("dark")
                    } label: {
                        SettingsLabel(title: L("Thème"), icon: "moon.fill", color: .indigo)
                    }
                    .pickerStyle(.navigationLink)
                } header: {
                    Text(L("Général"))
                }

                Section {
                    Picker(selection: $sleepMinutes) {
                        ForEach(sleepChoices, id: \.self) { minutes in
                            Text(minutes == 0 ? L("Désactivé") : L("{minutes} min", ["minutes": "\(minutes)"]))
                                .tag(minutes)
                        }
                    } label: {
                        SettingsLabel(title: L("Minuteur de sommeil"), icon: "moon.zzz.fill", color: .purple)
                    }
                    .pickerStyle(.navigationLink)
                    Toggle(isOn: $keepAwake) {
                        SettingsLabel(title: L("Écran allumé pendant les imports"), icon: "sun.max.fill", color: .orange)
                    }
                } header: {
                    Text(L("Lecteur"))
                } footer: {
                    if let end = player.sleepEnd {
                        Text(L("La lecture se met en pause à {time}.", ["time": end.formatted(date: .omitted, time: .shortened)]))
                    }
                }

                Section {
                    Picker(selection: $maxParallel) {
                        ForEach(1...3, id: \.self) { count in
                            Text("\(count)").tag(count)
                        }
                    } label: {
                        SettingsLabel(title: L("Imports simultanés"), icon: "square.stack.3d.down.right.fill",
                                      color: .ada)
                    }
                    Toggle(isOn: $skipDuplicates) {
                        SettingsLabel(title: L("Ignorer les doublons"), icon: "doc.on.doc.fill", color: .teal)
                    }
                    Toggle(isOn: $autoRetry) {
                        SettingsLabel(title: L("Nouvelles tentatives"), icon: "arrow.clockwise", color: .green)
                    }
                } header: {
                    Text(L("Imports"))
                }

                Section {
                    Toggle(isOn: $appLock) {
                        SettingsLabel(title: L("Verrouiller ADA'S à l'ouverture"), icon: "faceid", color: .green)
                    }
                    Button {
                        model.openVault()
                    } label: {
                        HStack {
                            SettingsLabel(title: L("Coffre"), icon: "lock.fill", color: .red)
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(Color(.tertiaryLabel))
                        }
                    }
                    .foregroundColor(.primary)
                } header: {
                    Text(L("Confidentialité"))
                } footer: {
                    Text(L("ADA'S demandera Face ID à chaque ouverture. Le Coffre est toujours protégé, même si cette option est désactivée."))
                }

                Section {
                    Button {
                        model.backup()
                    } label: {
                        SettingsLabel(title: L("Sauvegarder"), icon: "externaldrive.fill.badge.icloud", color: .blue)
                    }
                    .foregroundColor(.primary)
                    Button {
                        confirmRestore = true
                    } label: {
                        SettingsLabel(title: L("Restaurer"), icon: "arrow.counterclockwise", color: .gray)
                    }
                    .foregroundColor(.primary)
                } header: {
                    Text(L("Sauvegarde"))
                } footer: {
                    Text(L("La sauvegarde (sans les fichiers audio) va dans l'app Fichiers › ADA'S. Gardez-la ou envoyez-la sur un autre iPhone pour tout retrouver."))
                }

                Section {
                    ShareLink(item: L("J'utilise ADA'S pour importer et écouter ma musique. Essayez-la !")) {
                        SettingsLabel(title: L("Partager ADA'S"), icon: "square.and.arrow.up", color: .ada)
                    }
                    .foregroundColor(.primary)
                    if let mail = URL(string: "mailto:kwadioh@gmail.com?subject=ADA'S") {
                        Link(destination: mail) {
                            SettingsLabel(title: L("Envoyer un avis"), icon: "envelope.fill", color: .blue)
                        }
                        .foregroundColor(.primary)
                    }
                    NavigationLink {
                        HelpView()
                    } label: {
                        SettingsLabel(title: L("Aide"), icon: "questionmark.circle.fill", color: .orange)
                    }
                    NavigationLink {
                        AboutView()
                    } label: {
                        SettingsLabel(title: L("À propos"), icon: "info.circle.fill", color: .gray)
                    }
                } footer: {
                    Text(storage)
                }
            }
            .navigationTitle(L("Réglages"))
            .onAppear {
                storage = model.storageText()
                if player.sleepEnd == nil { sleepMinutes = 0 }
            }
            .onChange(of: sleepMinutes) { minutes in
                player.setSleepTimer(minutes: minutes)
                if minutes > 0 {
                    model.show(L("La lecture sera mise en pause dans {minutes} min.", ["minutes": "\(minutes)"]), .ok)
                }
            }
            .onChange(of: language) { _ in
                model.setEngineLanguage()
            }
            .onChange(of: maxParallel) { _ in model.applyEngineSettings() }
            .onChange(of: skipDuplicates) { _ in model.applyEngineSettings() }
            .onChange(of: autoRetry) { _ in model.applyEngineSettings() }
            .confirmationDialog(L("Restaurer la sauvegarde ?"), isPresented: $confirmRestore, titleVisibility: .visible) {
                Button(L("Restaurer")) { model.restore() }
                Button(L("Annuler"), role: .cancel) {}
            } message: {
                Text(L("La bibliothèque et les playlists actuelles seront remplacées par celles de la sauvegarde."))
            }
        }
    }
}

struct HelpItem: Identifiable {
    let question: String
    let answer: String
    var id: String { question }
}

struct HelpView: View {
    private let items: [HelpItem] = [
        HelpItem(question: "Comment importer une musique ?",
                 answer: "Dans YouTube (ou un autre site), touchez « Partager » puis « Copier le lien ». Revenez dans ADA'S et touchez « Coller un lien » sur l'accueil. Vous pouvez aussi chercher un titre dans l'onglet Rechercher."),
        HelpItem(question: "Où sont mes fichiers ?",
                 answer: "Dans l'app Fichiers › Sur mon iPhone › ADA'S. Les morceaux du Coffre n'y apparaissent pas."),
        HelpItem(question: "Comment cacher un morceau ?",
                 answer: "Sur l'accueil, touchez ⋯ sur le morceau puis « Mettre au Coffre ». Il n'apparaît plus que dans le Coffre, protégé par Face ID."),
        HelpItem(question: "Comment réduire la taille d'un morceau ?",
                 answer: "Touchez ⋯ puis « Compresser ». Si le gain est trop faible, le fichier d'origine est gardé."),
        HelpItem(question: "Comment écouter sur une enceinte ou une TV ?",
                 answer: "Ouvrez le lecteur (touchez le morceau en bas de l'écran) puis le bouton AirPlay."),
        HelpItem(question: "Quels liens puis-je importer ?",
                 answer: "YouTube et la plupart des sites de vidéos ou de musique publics. Les contenus protégés (DRM) ne peuvent pas être importés."),
    ]

    var body: some View {
        List {
            ForEach(items) { item in
                DisclosureGroup {
                    Text(L(item.answer))
                        .font(.system(size: 15))
                        .foregroundColor(.secondary)
                        .padding(.vertical, 4)
                } label: {
                    Text(L(item.question))
                        .font(.system(size: 16, weight: .medium))
                }
                .tint(.ada)
            }
        }
        .navigationTitle(L("Aide"))
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct AboutView: View {
    @EnvironmentObject var model: AppModel

    private var version: String {
        let info = Bundle.main.infoDictionary
        return info?["CFBundleShortVersionString"] as? String ?? "2.0"
    }

    private var engineText: String {
        if model.engineReady {
            let python: String = model.engineInfo["python"] ?? ""
            return "Python " + python
        }
        return model.engineError == nil ? L("Démarrage…") : L("Erreur")
    }

    var body: some View {
        List {
            Section {
                VStack(spacing: 10) {
                    Image("Logo")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 110, height: 110)
                        .clipShape(RoundedRectangle(cornerRadius: 26, style: .continuous))
                        .shadow(color: .black.opacity(0.2), radius: 10, y: 4)
                    Text("ADA'S")
                        .font(.system(size: 30, weight: .bold))
                    Text(L("Version {version}", ["version": version]))
                        .foregroundColor(.secondary)
                    Text(L("Votre musique, importée et rangée."))
                        .font(.system(size: 16))
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 18)
                .listRowBackground(Color.clear)
            }
            Section {
                LabeledContent(L("Moteur"), value: engineText)
                LabeledContent("yt-dlp", value: model.engineInfo["ytdlp"] ?? "—")
                LabeledContent(L("JavaScript (YouTube)"), value: model.engineInfo["javascript"] == "1" ? "JavaScriptCore" : "—")
            } header: {
                Text(L("Moteur d'import"))
            } footer: {
                if let error = model.engineError {
                    Text(error)
                        .font(.system(size: 11, design: .monospaced))
                        .textSelection(.enabled)
                }
            }
        }
        .navigationTitle(L("À propos"))
        .navigationBarTitleDisplayMode(.inline)
    }
}
