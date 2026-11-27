import SwiftUI

/// S20: a folding section. Open state lives in `AppSettings.openFolds` (R-82), never in the view. When the
/// open/close animation finishes the host re-places the panel (R-64), through `services.foldAnimationDone`.
enum FoldID {
    static let detail = "detail"
    static let projects = "projects"
    static func person(_ installId: String) -> FoldId { "person:\(installId)" }
}

struct Fold<Content: View>: View {
    let id: FoldId
    let title: String
    let trailing: String?
    /// Greys the header (a stale person).
    let stale: Bool
    let open: Bool
    let palette: Palette
    let setFold: (FoldId, Bool) -> Void
    @ViewBuilder let content: () -> Content
    @EnvironmentObject private var services: PanelServices

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.lineGap) {
            Button {
                withAnimation(.easeInOut(duration: Layout.animation)) { setFold(id, !open) }
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: "chevron.right").font(.system(size: 9, weight: .semibold))
                        .rotationEffect(.degrees(open ? 90 : 0))
                    Text(title).font(Layout.body)
                    Spacer(minLength: 6)
                    if let trailing { Text(trailing).font(Layout.small.monospacedDigit()) }
                }
                .foregroundColor(stale ? palette.grey : palette.muted)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open { content().padding(.leading, 14) }
        }
        .modifier(AnimationDone(target: open ? 1 : 0, done: services.foldAnimationDone))
    }
}

/// Calls `done` when an animated change of `target` reaches its end value (macOS 13 has no animation
/// completion callback; SwiftUI interpolates `animatableData` frame by frame and the last frame equals target).
struct AnimationDone: ViewModifier, Animatable {
    let target: Double
    let done: () -> Void
    var animatableData: Double {
        didSet { if animatableData == target { DispatchQueue.main.async(execute: done) } }
    }

    init(target: Double, done: @escaping () -> Void) {
        self.target = target
        self.done = done
        animatableData = target
    }

    func body(content: Content) -> some View { content }
}

/// A two-column line inside a fold.
struct FoldRow: View {
    let title: String
    let value: String
    let palette: Palette

    var body: some View {
        HStack(spacing: 6) {
            Text(title).lineLimit(1).truncationMode(.middle).foregroundColor(palette.ink)
            Spacer(minLength: 6)
            Text(value).monospacedDigit().lineLimit(1).foregroundColor(palette.muted)
        }
        .font(Layout.small)
    }
}
