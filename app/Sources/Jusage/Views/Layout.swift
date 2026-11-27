import SwiftUI

/// S17: the panel's measurements, one place. Every view reads these; none carries its own number.
enum Layout {
    static let width: CGFloat = 312
    static let padding: CGFloat = 14
    /// Space between blocks (accounts, folds) and between lines inside one.
    static let blockGap: CGFloat = 10
    static let lineGap: CGFloat = 4
    /// Meter row columns: optional label, bar, bold %, countdown.
    static let labelColumn: CGFloat = 92
    static let pctColumn: CGFloat = 38
    static let countdownColumn: CGFloat = 54
    static let barHeight: CGFloat = 8
    /// The bright fill is never thinner than this, so 1% still shows (R-71).
    static let minUsedFill: CGFloat = 3
    static let lineWidth: CGFloat = 1.5
    /// Fold open/close (R-64: the panel re-places when this animation completes).
    static let animation: Double = 0.18
    static let small: Font = .system(size: 11)
    static let body: Font = .system(size: 12)
    static let pct: Font = .system(size: 12, weight: .bold).monospacedDigit()
}
