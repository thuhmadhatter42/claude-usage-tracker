import AppKit
import SwiftUI
import Combine

/// The accent colour: one colour the user picks, that every lit thing in the app is drawn in.
/// Lifted from Jlipper (Jlipper/App/Accent.swift), with Beat Key's High Contrast switch
/// (beatkey/app/Sources/Orion/Faceplate/Accent.swift, Settings.swift). House default: Artemis amber #ffb020.
/// sRGB 0...255, one owner (`AccentSettings.shared`), saved in UserDefaults, picked from presets or the
/// macOS colour wheel, every view reading `@Environment(\.accent)`.
struct Accent: Hashable, Sendable {
    var r: Double
    var g: Double
    var b: Double

    init(_ r: Double, _ g: Double, _ b: Double) {
        self.r = min(255, max(0, r)); self.g = min(255, max(0, g)); self.b = min(255, max(0, b))
    }

    /// The house accent: Artemis amber #ffb020 (J 2026-11-18). The default in every app.
    static let amber = Accent(255, 176, 32)
    /// The dark surface the accent sits on. Dark only (J 2026-11-18).                       CHANGE if the app's
    /// main surface is lighter than #1e1e1e (a lighter surface needs a brighter accent to read).
    static let base = Accent(30, 30, 30)
    /// Text in the accent keeps at least this contrast on the base (WCAG AA for normal text). A darker pick is
    /// lifted (brighter first, then paler) until it reaches it.
    static let minContrast = 4.5
    static let black = Accent(0, 0, 0)
    static let white = Accent(255, 255, 255)

    /// The swatches in Settings. Amber first. Every one reads on the base without lifting (AccentTests checks).
    static let presets: [(name: String, accent: Accent)] = [
        ("Amber", .amber),
        ("Tally Red", Accent(255, 99, 82)),
        ("Lime", Accent(166, 226, 46)),
        ("Mint", Accent(60, 225, 150)),
        ("Sky", Accent(80, 170, 255)),
        ("Violet", Accent(170, 130, 255)),
        ("Pink", Accent(255, 105, 180)),
        ("White", Accent(235, 235, 240)),
    ]

    var color: Color { Color(.sRGB, red: r / 255, green: g / 255, blue: b / 255, opacity: 1) }
    func color(_ opacity: Double) -> Color { Color(.sRGB, red: r / 255, green: g / 255, blue: b / 255, opacity: opacity) }
    var nsColor: NSColor { NSColor(srgbRed: r / 255, green: g / 255, blue: b / 255, alpha: 1) }
    /// "#ffb020"
    var hex: String { String(format: "#%02x%02x%02x", Int(r.rounded()), Int(g.rounded()), Int(b.rounded())) }

    /// WCAG relative luminance, 0...1.
    var luminance: Double {
        func lin(_ c: Double) -> Double {
            let s = c / 255
            return s <= 0.03928 ? s / 12.92 : pow((s + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    }

    /// WCAG contrast ratio, 1...21.
    func contrast(with o: Accent) -> Double {
        let (a, b) = (luminance, o.luminance)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)
    }

    /// Text on a fill of this colour: black or white, whichever has more contrast.
    var ink: Accent { contrast(with: .black) >= contrast(with: .white) ? .black : .white }

    /// The colour as the app shows it: unchanged when it reads on the base, else lifted to `minContrast`
    /// (HSB brightness up first, keeping hue and saturation; then less saturated). `clamped` = it had to be lifted.
    func readable() -> (accent: Accent, clamped: Bool) {
        if contrast(with: .base) >= Accent.minContrast { return (self, false) }
        var (h, s, v) = hsb
        func at(_ s: Double, _ v: Double) -> Accent { Accent.fromHSB(h, s, v) }
        func ok(_ a: Accent) -> Bool { a.contrast(with: .base) >= Accent.minContrast }
        if ok(at(s, 1)) {
            var lo = v, hi = 1.0
            for _ in 0..<24 { let m = (lo + hi) / 2; if ok(at(s, m)) { hi = m } else { lo = m } }
            v = hi
        } else {
            v = 1
            var lo = 0.0, hi = s
            for _ in 0..<24 { let m = (lo + hi) / 2; if ok(at(m, 1)) { lo = m } else { hi = m } }
            s = lo
        }
        if h.isNaN { h = 0 }
        return (at(s, v), true)
    }

    /// Hue 0...1, saturation 0...1, brightness 0...1.
    var hsb: (Double, Double, Double) {
        let (R, G, B) = (r / 255, g / 255, b / 255)
        let mx = max(R, G, B), mn = min(R, G, B), d = mx - mn
        var h = 0.0
        if d > 0 {
            if mx == R { h = ((G - B) / d).truncatingRemainder(dividingBy: 6) }
            else if mx == G { h = (B - R) / d + 2 }
            else { h = (R - G) / d + 4 }
            h /= 6
            if h < 0 { h += 1 }
        }
        return (h, mx == 0 ? 0 : d / mx, mx)
    }

    static func fromHSB(_ h: Double, _ s: Double, _ v: Double) -> Accent {
        let i = floor(h * 6), f = h * 6 - i
        let p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s)
        let (R, G, B): (Double, Double, Double)
        switch Int(i) % 6 {
        case 0: (R, G, B) = (v, t, p)
        case 1: (R, G, B) = (q, v, p)
        case 2: (R, G, B) = (p, v, t)
        case 3: (R, G, B) = (p, q, v)
        case 4: (R, G, B) = (t, p, v)
        default: (R, G, B) = (v, p, q)
        }
        return Accent(R * 255, G * 255, B * 255)
    }

    /// From any NSColor (the panel may hand back a catalogue or generic colour); nil = no sRGB form.
    init?(_ ns: NSColor) {
        guard let c = ns.usingColorSpace(.sRGB) else { return nil }
        self.init(Double(c.redComponent) * 255, Double(c.greenComponent) * 255, Double(c.blueComponent) * 255)
    }

    var described: String { "rgb(\(Int(r.rounded())),\(Int(g.rounded())),\(Int(b.rounded())))" }
}

/// The chosen colour, remembered between launches (UserDefaults `accentColor`: [r, g, b] sRGB 0...255; the same
/// key Jlipper and Beat Key use), and the High Contrast switch (UserDefaults `highContrast`).
/// The one owner of the colour in use: `accentRoot()` puts `effective` in the environment and SwiftUI redraws
/// every view that read it when it changes.
@MainActor
final class AccentSettings: ObservableObject {
    static let key = "accentColor"
    static let highContrastKey = "highContrast"
    static let shared = AccentSettings()
    /// While the colour wheel drags, updates are drawn at most this often.
    static let minInterval = 1.0 / 30

    /// What the user picked (the wheel shows this).
    @Published private(set) var picked: Accent = .amber
    /// What the app shows: `picked`, lifted if it would not read on the dark base.
    @Published private(set) var effective: Accent = .amber
    /// The colour wheel is open.
    @Published var panelOpen = false
    /// High Contrast (Beat Key): brighter secondary text, no glow. Lit shapes stay.
    @Published var highContrast = false {
        didSet {
            guard highContrast != oldValue else { return }
            defaults.set(highContrast, forKey: AccentSettings.highContrastKey)
            AppLog.shared.info("accent", "high contrast \(highContrast ? "on" : "off")")
        }
    }
    private(set) var clamped = false

    private let defaults: UserDefaults
    private var pending: Accent?
    private var lastApply = Date.distantPast

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        highContrast = defaults.bool(forKey: AccentSettings.highContrastKey)
        apply(AccentSettings.load(defaults) ?? .amber, persist: false)
    }

    /// The saved pick; nil = none saved or unreadable (logged).
    static func load(_ d: UserDefaults) -> Accent? {
        guard let raw = d.object(forKey: key) else { return nil }
        guard let a = raw as? [Double], a.count == 3, a.allSatisfy({ $0.isFinite }) else {
            AppLog.shared.error("accent", "stored \(key) unreadable (\(raw)), using amber")
            return nil
        }
        return Accent(a[0], a[1], a[2])
    }

    /// A new colour, now (a swatch, tests).
    func set(_ a: Accent) { pending = nil; apply(a, persist: true) }

    /// Back to amber; the stored pick is removed.
    func reset() {
        pending = nil
        apply(.amber, persist: false)
        defaults.removeObject(forKey: AccentSettings.key)
        AppLog.shared.info("accent", "reset to amber")
    }

    /// From the colour wheel while it drags: coalesced to `minInterval`, the last one always lands.
    func drag(_ a: Accent) {
        let wait = AccentSettings.minInterval - Date().timeIntervalSince(lastApply)
        if wait <= 0 { set(a); return }
        let scheduled = pending != nil
        pending = a
        if !scheduled {
            DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self] in
                MainActor.assumeIsolated {
                    guard let self, let p = self.pending else { return }
                    self.set(p)
                }
            }
        }
    }

    private func apply(_ a: Accent, persist: Bool) {
        let (shown, wasClamped) = a.readable()
        // logged once each time a pick crosses into too-dark, not on every drag step
        if wasClamped && !clamped {
            AppLog.shared.info("accent", "\(a.described) too dark on the base (\(String(format: "%.1f", a.contrast(with: .base))):1), shown as \(shown.described)")
        }
        clamped = wasClamped
        lastApply = Date()
        if picked != a { picked = a }
        if effective != shown { effective = shown }
        if persist {
            defaults.set([a.r, a.g, a.b], forKey: AccentSettings.key)
            // UserDefaults.set has no error path: read it back so a failed save is in the log, not silent
            if (defaults.array(forKey: AccentSettings.key) as? [Double]) != [a.r, a.g, a.b] {
                AppLog.shared.error("accent", "save failed: \(AccentSettings.key) did not read back as \(a.described)")
            }
        }
    }
}

/// The macOS colour wheel (the shared NSColorPanel, wheel mode, no opacity), with Reset to Amber under it.
/// Opened from the picker's Custom… button; the app recolours live while the wheel drags.
@MainActor
final class AccentPanel: NSObject {
    static let shared = AccentPanel()
    private var closeObserver: Any?
    private let settings: AccentSettings

    /// `settings: nil` = the app's one owner.
    init(settings: AccentSettings? = nil) { self.settings = settings ?? .shared }

    func toggle() {
        if settings.panelOpen { NSColorPanel.shared.close() } else { open() }
    }

    func open() {
        let p = NSColorPanel.shared
        p.mode = .wheel
        p.showsAlpha = false
        p.isContinuous = true
        p.setTarget(nil); p.setAction(nil)        // setting the colour must not echo back as a pick
        p.color = settings.picked.nsColor
        p.setTarget(self)
        p.setAction(#selector(changed(_:)))
        p.accessoryView = resetView()
        p.title = "\(House.appName) Accent Colour"
        if closeObserver == nil {
            closeObserver = NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: p,
                                                                   queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.closed() }
            }
        }
        NSApp.activate(ignoringOtherApps: true)
        p.orderFront(nil)
        settings.panelOpen = true
        AppLog.shared.info("accent", "colour panel open (\(settings.picked.described))")
    }

    @objc private func changed(_ sender: NSColorPanel) {
        guard let a = Accent(sender.color) else {
            AppLog.shared.error("accent", "panel colour \(sender.color) has no sRGB form, ignored")
            return
        }
        settings.drag(a)
    }

    @objc private func resetPressed(_ sender: Any?) {
        let p = NSColorPanel.shared
        p.setTarget(nil); p.setAction(nil)
        p.color = Accent.amber.nsColor
        p.setTarget(self); p.setAction(#selector(changed(_:)))
        settings.reset()
    }

    /// Keeps the wheel in step when a swatch is clicked while it is open.
    func sync() {
        guard settings.panelOpen else { return }
        let p = NSColorPanel.shared
        p.setTarget(nil); p.setAction(nil)
        p.color = settings.picked.nsColor
        p.setTarget(self); p.setAction(#selector(changed(_:)))
    }

    private func closed() {
        let p = NSColorPanel.shared
        p.setTarget(nil); p.setAction(nil)
        p.accessoryView = nil
        if let o = closeObserver { NotificationCenter.default.removeObserver(o); closeObserver = nil }
        settings.panelOpen = false
        AppLog.shared.info("accent", "colour panel closed, \(settings.picked.described)"
                           + (settings.clamped ? " (shown as \(settings.effective.described))" : ""))
    }

    private func resetView() -> NSView {
        let b = NSButton(title: "Reset to Amber", target: self, action: #selector(resetPressed(_:)))
        b.bezelStyle = .rounded
        b.controlSize = .small
        b.sizeToFit()
        let v = NSView(frame: NSRect(x: 0, y: 0, width: b.frame.width + 16, height: b.frame.height + 12))
        b.setFrameOrigin(NSPoint(x: 8, y: 6))
        v.addSubview(b)
        return v
    }
}

// MARK: - Environment

private struct AccentKey: EnvironmentKey {
    static let defaultValue: Accent = .amber
}

private struct HighContrastKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// The accent in use (`AccentSettings.shared.effective`), set by `accentRoot()` at every window and sheet root.
    var accent: Accent {
        get { self[AccentKey.self] }
        set { self[AccentKey.self] = newValue }
    }

    /// High Contrast is on: no glow, brighter secondary text.
    var highContrast: Bool {
        get { self[HighContrastKey.self] }
        set { self[HighContrastKey.self] = newValue }
    }
}

/// A surface's root (main window, Settings, every sheet): puts the accent and High Contrast in the environment,
/// tints the system controls, and forces dark (the house is dark only).
struct AccentRoot: ViewModifier {
    @ObservedObject private var accent = AccentSettings.shared
    func body(content: Content) -> some View {
        content
            .environment(\.accent, accent.effective)
            .environment(\.highContrast, accent.highContrast)
            .tint(accent.effective.color)
            .preferredColorScheme(.dark)
    }
}

extension View {
    /// Put this on the root view of every window and sheet.
    func accentRoot() -> some View { modifier(AccentRoot()) }
}

// MARK: - The picker (Settings › Appearance)

/// The colour row for Settings: the preset swatches, Custom… (the macOS colour wheel), Reset, and High Contrast.
struct AccentPickerRow: View {
    @ObservedObject private var settings = AccentSettings.shared
    @Environment(\.accent) private var theme

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                ForEach(Accent.presets, id: \.name) { p in
                    Button {
                        settings.set(p.accent)
                        AccentPanel.shared.sync()
                    } label: {
                        Circle().fill(p.accent.color).frame(width: 20, height: 20)
                            .overlay(Circle().strokeBorder(Color.white.opacity(settings.picked == p.accent ? 0.95 : 0.15),
                                                           lineWidth: settings.picked == p.accent ? 2 : 1))
                    }
                    .buttonStyle(.plain)
                    .help(p.name)
                    .accessibilityLabel(p.name)
                    .accessibilityAddTraits(settings.picked == p.accent ? [.isSelected] : [])
                }
                Button(settings.panelOpen ? "Close Wheel" : "Custom…") { AccentPanel.shared.toggle() }
                Button("Reset") { settings.reset(); AccentPanel.shared.sync() }
                    .disabled(settings.picked == .amber)
            }
            if settings.clamped {
                Text("Too dark to read here. Shown brighter.")
                    .font(.caption).foregroundColor(theme.color)
            }
            Toggle("High Contrast", isOn: $settings.highContrast)
                .help("Brighter text, no glow.")
        }
    }
}
