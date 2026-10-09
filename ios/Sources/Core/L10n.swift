import Foundation

/// Langue de l'interface : français (texte de référence) ou anglais.
/// Chaque texte affiché passe par L("Texte en français") ; en anglais il
/// est cherché dans EN (Strings_en.swift), sinon le français s'affiche.
enum Lang {
    static var code = "fr"

    static func resolve(_ choice: String) -> String {
        if choice == "fr" || choice == "en" { return choice }
        let preferred = Locale.preferredLanguages
        for identifier in preferred {
            let base = identifier.split(separator: "-").first.map(String.init)?.lowercased() ?? ""
            if base == "fr" || base == "en" { return base }
        }
        return preferred.isEmpty ? "fr" : "en"
    }

    static func apply(_ choice: String) {
        code = resolve(choice)
    }

    static func name(_ choice: String) -> String {
        switch choice {
        case "fr": return "Français"
        case "en": return "English"
        default: return L("Langue du téléphone")
        }
    }
}

func L(_ french: String) -> String {
    guard Lang.code == "en" else { return french }
    return EN[french] ?? french
}

func L(_ french: String, _ values: [String: String]) -> String {
    var text = L(french)
    for (key, value) in values {
        text = text.replacingOccurrences(of: "{" + key + "}", with: value)
    }
    return text
}
