import SwiftUI

/// The house glow: the Artemis amber halo, in the user's accent, lit only when the thing is active.
///
/// Artemis' glow is the ONLY full-strength base (J 2026-11-18). In a native app the halo is drawn at a fraction
/// of it. TWO constants, set by J on 2026-11-18 ("12% reduce radius by 50%"):
///   opacity = 0.12 x Artemis full        radius = 0.06 x Artemis full
/// Call sites always pass FULL-strength Artemis numbers (a `HouseGlow.Full` preset); the two constants below
/// are the only place the strength is tuned.
enum HouseGlow {
    /// Opacity: 12% of full.
    static let opacityScale: Double = 0.12
    /// Radius: 6% of full (the opacity factor, halved).
    static let radiusScale: CGFloat = 0.06

    /// One full-strength halo layer. `radius` is a SwiftUI shadow radius (a CSS blur of 2x that).
    struct Layer: Equatable {
        var radius: CGFloat
        var opacity: Double
    }

    /// Full-strength halos, before scaling. Every value is Artemis' own (`~/artemis/app/src/faceplate/`):
    /// `--amber-glow` is `rgba(255,176,32,.6)` (shared.css:6), so each layer is opacity .6 unless the CSS says otherwise.
    /// A CSS box-shadow/text-shadow blur of B px is a SwiftUI shadow radius of B/2 (the spread is not drawn).
    enum Full {
        /// Lit text or a readout (a meter value): faceplate.css:143 `.lcd-hero .value`,
        /// `text-shadow: 0 0 6px var(--amber-glow), 0 0 16px rgba(var(--amber-rgb),.4)`.
        static let litText: [Layer] = [Layer(radius: 3, opacity: 0.6), Layer(radius: 8, opacity: 0.4)]
        /// An LED / lit meter segment: faceplate.css:302 `.meter-h .seg.lit-amber`,
        /// `box-shadow: 0 0 3px 1px var(--amber-glow)`. (faceplate.css:333 `.bay.drop-*` marker is 6px, same colour.)
        static let led: [Layer] = [Layer(radius: 1.5, opacity: 0.6)]
        /// A lit button, the one main action: faceplate.css:913 `.ssl-btn.on.amber`,
        /// `box-shadow: 0 0 11px 2px var(--amber-glow)`. (faceplate.css:1064 `.btn.primary` is 9px, same colour.)
        static let litButton: [Layer] = [Layer(radius: 5.5, opacity: 0.6)]
        /// A lit chip or preset (a listening key, a chosen option): faceplate.css:528 `.in-pre.on`,
        /// `box-shadow: 0 0 5px 1px var(--amber-glow)`.
        static let litChip: [Layer] = [Layer(radius: 2.5, opacity: 0.6)]
    }

    /// What is actually drawn for one full-strength layer.
    static func scaled(_ full: Layer) -> Layer {
        Layer(radius: full.radius * radiusScale, opacity: full.opacity * opacityScale)
    }
}

extension View {
    /// The accent halo. `active: false` draws nothing: illumination only when the thing is on (Artemis rule).
    /// `color` overrides the accent (a red fault, a green ready lamp). High Contrast draws no halo.
    func accentGlow(_ full: [HouseGlow.Layer] = HouseGlow.Full.litChip, active: Bool = true, color: Color? = nil) -> some View {
        modifier(AccentGlow(full: full, active: active, color: color))
    }

    /// The same, from raw full-strength Artemis numbers (e.g. `accentGlow(radius: 5.5, opacity: 0.6)`).
    func accentGlow(radius: CGFloat, opacity: Double, active: Bool = true, color: Color? = nil) -> some View {
        modifier(AccentGlow(full: [HouseGlow.Layer(radius: radius, opacity: opacity)], active: active, color: color))
    }
}

private struct AccentGlow: ViewModifier {
    let full: [HouseGlow.Layer]
    let active: Bool
    let color: Color?
    @Environment(\.accent) private var theme
    @Environment(\.highContrast) private var highContrast

    func body(content: Content) -> some View {
        let on = active && !highContrast
        let base = color ?? theme.color
        // Up to two layers (every Artemis halo is one or two); a fixed shape keeps the view's identity stable.
        let a = full.indices.contains(0) ? HouseGlow.scaled(full[0]) : HouseGlow.Layer(radius: 0, opacity: 0)
        let b = full.indices.contains(1) ? HouseGlow.scaled(full[1]) : HouseGlow.Layer(radius: 0, opacity: 0)
        return content
            .shadow(color: on ? base.opacity(a.opacity) : .clear, radius: on ? a.radius : 0)
            .shadow(color: on ? base.opacity(b.opacity) : .clear, radius: on ? b.radius : 0)
    }
}

// MARK: - The two house button styles (crisp: a solid fill or a 1 px border, sharp text, the faint halo)

/// The one main action on a screen: a solid accent fill, ink text picked by contrast, a 1 px edge, the halo.
struct AccentButtonStyle: ButtonStyle {
    @Environment(\.accent) private var theme
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: 6, style: .continuous)
        return configuration.label
            .lineLimit(1).fixedSize()   // a button label never wraps
            .font(.body.weight(.semibold))
            .foregroundColor(theme.ink.color)
            .padding(.horizontal, 12).padding(.vertical, 4)
            .background(shape.fill(theme.color))
            .overlay(shape.fill(theme.ink.color.opacity(configuration.isPressed ? 0.18 : 0)))
            .overlay(shape.strokeBorder(theme.ink.color.opacity(0.12), lineWidth: 1))
            .contentShape(shape)
            .accentGlow(HouseGlow.Full.litButton, active: enabled)
            .opacity(enabled ? 1 : 0.4)
    }
}

/// Every other push button: no fill, a 1 px accent border, accent text. Pressed fills faintly.
struct CrispButtonStyle: ButtonStyle {
    @Environment(\.accent) private var theme
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: 6, style: .continuous)
        return configuration.label
            .lineLimit(1).fixedSize()
            .foregroundColor(theme.color)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(shape.fill(theme.color.opacity(configuration.isPressed ? 0.22 : 0.08)))
            .overlay(shape.strokeBorder(theme.color.opacity(0.85), lineWidth: 1))
            .contentShape(shape)
            .accentGlow(HouseGlow.Full.litChip, active: enabled)
            .opacity(enabled ? 1 : 0.4)
    }
}

extension ButtonStyle where Self == AccentButtonStyle {
    static var accent: AccentButtonStyle { AccentButtonStyle() }
}

extension ButtonStyle where Self == CrispButtonStyle {
    static var crisp: CrispButtonStyle { CrispButtonStyle() }
}
