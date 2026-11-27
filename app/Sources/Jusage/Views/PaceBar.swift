import SwiftUI

/// S19: the bullet bar (R-71). Neutral track; dark fill to the forecast landing; bright fill to used (at least
/// 3 pt when anything is used); a thin white line at time gone. Grey tone: grey fill, no dark fill.
struct PaceBar: View {
    let model: MeterRowModel
    let palette: Palette

    var body: some View {
        GeometryReader { g in
            let w = g.size.width, h = g.size.height
            let fill = palette.fill(model.tone)
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: h / 2).fill(palette.track)
                if let shadow = model.shadow {
                    RoundedRectangle(cornerRadius: h / 2).fill(fill.opacity(0.35)).frame(width: w * shadow)
                }
                if model.used > 0 {
                    RoundedRectangle(cornerRadius: h / 2).fill(fill)
                        .frame(width: min(w, max(w * model.used, Layout.minUsedFill)))
                }
                if let line = model.line {
                    Rectangle().fill(palette.paceLine)
                        .frame(width: Layout.lineWidth, height: h + 4)
                        .offset(x: min(max(w * line - Layout.lineWidth / 2, 0), w - Layout.lineWidth))
                }
            }
            .frame(height: h)
        }
        .frame(height: Layout.barHeight)
    }
}
