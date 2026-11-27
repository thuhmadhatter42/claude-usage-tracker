import Foundation

/// S4: themes (R-47, R-86, R-134, R-170). Same validation as the engine's E27: a bad file refuses and names the
/// theme and key. System is built in, so the picker is never empty.
struct ThemeError: Error, Equatable, LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct RGB: Equatable {
    let r: Double, g: Double, b: Double
}

struct Theme: Equatable {
    enum Scheme: String { case light, dark }

    let id: String
    let name: String
    /// nil for System: the app follows the Mac's appearance and uses system colours.
    let scheme: Scheme?
    /// Hex colours by key (`bg`, `sf`, `sf2`, `ink`, `ink2`, `mut`, `line`, `ok`, `warn`, `hot`, `accent`, and the optional `grid`, `seg`, `sel`).
    let colours: [String: String]

    static let system = Theme(id: "system", name: "System", scheme: nil, colours: [:])

    func rgb(_ key: String) -> RGB? { colours[key].flatMap(Theme.parseHex) }

    static func parseHex(_ s: String) -> RGB? {
        guard s.count == 7, s.hasPrefix("#"), let v = UInt32(s.dropFirst(), radix: 16) else { return nil }
        return RGB(r: Double((v >> 16) & 0xFF) / 255, g: Double((v >> 8) & 0xFF) / 255, b: Double(v & 0xFF) / 255)
    }

    /// The theme with this id from `book`, else System.
    static func resolve(id: String, book: ThemeBook = .current) -> Theme {
        book.themes.first { $0.id == id } ?? .system
    }
}

struct ThemeBook: Equatable {
    /// In picker order; always contains System.
    let themes: [Theme]

    /// What the app uses until S9 loads the shipped file and assigns it here.
    nonisolated(unsafe) static var current = ThemeBook(themes: [.system])

    static let requiredKeys = ["scheme", "bg", "sf", "sf2", "ink", "ink2", "mut", "line", "ok", "warn", "hot", "accent"]
    private static let colourKeys = ["bg", "sf", "sf2", "ink", "ink2", "mut", "line", "ok", "warn", "hot", "accent"]
    private static let optionalColourKeys = ["grid", "seg", "sel"]

    private static func bad(_ problem: String) -> ThemeError { ThemeError(message: "themes.json: \(problem)") }

    static func load(url: URL) throws -> ThemeBook {
        let data: Data
        do { data = try Data(contentsOf: url) }
        catch { throw bad(FileManager.default.fileExists(atPath: url.path) ? "cannot be read (\(error.localizedDescription))" : "file is missing") }
        return try parse(data)
    }

    static func parse(_ data: Data) throws -> ThemeBook {
        let any: Any
        do { any = try JSONSerialization.jsonObject(with: data) }
        catch { throw bad("is not valid JSON (\(error.localizedDescription))") }
        guard let top = any as? [String: Any] else { throw bad("top level must be an object") }
        guard let order = top["order"] as? [String], !order.isEmpty else { throw bad("'order' must be a non-empty list of theme ids") }
        guard let themes = top["themes"] as? [String: Any] else { throw bad("'themes' must be an object") }
        for id in order where themes[id] == nil { throw bad("order lists '\(id)' but there is no such theme") }
        guard themes["system"] != nil else { throw bad("theme 'system' is missing") }

        var built: [Theme] = []
        for id in order {
            guard let t = themes[id] as? [String: Any] else { throw bad("theme '\(id)' must be an object") }
            let name = (t["name"] as? String) ?? id
            if id == "system" { built.append(Theme(id: id, name: name, scheme: nil, colours: [:])); continue }
            for key in requiredKeys where t[key] == nil { throw bad("theme '\(id)' lacks '\(key)'") }
            guard let schemeText = t["scheme"] as? String, let scheme = Theme.Scheme(rawValue: schemeText) else {
                throw bad("theme '\(id)' scheme must be 'light' or 'dark'")
            }
            var colours: [String: String] = [:]
            for key in colourKeys + optionalColourKeys.filter({ t[$0] != nil }) {
                guard let hex = t[key] as? String, Theme.parseHex(hex) != nil else {
                    throw bad("theme '\(id)' key '\(key)' is not a #RRGGBB colour")
                }
                colours[key] = hex
            }
            built.append(Theme(id: id, name: name, scheme: scheme, colours: colours))
        }
        // Themes outside `order` are validated too (the engine does), but are not offered.
        for (id, value) in themes where !order.contains(id) {
            guard let t = value as? [String: Any] else { throw bad("theme '\(id)' must be an object") }
            if id == "system" { continue }
            for key in requiredKeys where t[key] == nil { throw bad("theme '\(id)' lacks '\(key)'") }
        }
        if !built.contains(where: { $0.id == "system" }) { built.insert(.system, at: 0) }   // never an empty picker
        return ThemeBook(themes: built)
    }
}
