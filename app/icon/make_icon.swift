// Draws jusage's app icon (1024 px PNG): three bullet-graph meters, like the menu bar panel.
import AppKit
let S: CGFloat = 1024
let img = NSImage(size: NSSize(width: S, height: S))
img.lockFocus()
let ctx = NSGraphicsContext.current!.cgContext
// macOS icon grid: 824 body centred, corner ~185
let body = NSRect(x: 100, y: 100, width: 824, height: 824)
let shape = NSBezierPath(roundedRect: body, xRadius: 185, yRadius: 185)
ctx.saveGState()
let sh = NSShadow(); sh.shadowColor = NSColor.black.withAlphaComponent(0.35); sh.shadowBlurRadius = 24; sh.shadowOffset = NSSize(width: 0, height: -10); sh.set()
NSColor(red: 0.07, green: 0.08, blue: 0.12, alpha: 1).setFill(); shape.fill()
ctx.restoreGState()
shape.addClip()
NSGradient(colors: [NSColor(red: 0.16, green: 0.18, blue: 0.27, alpha: 1), NSColor(red: 0.05, green: 0.06, blue: 0.10, alpha: 1)])!.draw(in: body, angle: -90)
// meters: (used, projected, elapsed tick, color)
let meters: [(CGFloat, CGFloat, CGFloat, NSColor)] = [
  (0.38, 0.62, 0.55, NSColor(red: 0.30, green: 0.85, blue: 0.55, alpha: 1)),
  (0.62, 0.92, 0.70, NSColor(red: 1.00, green: 0.78, blue: 0.25, alpha: 1)),
  (0.80, 1.00, 0.58, NSColor(red: 1.00, green: 0.38, blue: 0.36, alpha: 1)),
]
let x0: CGFloat = 220, W: CGFloat = 584, H: CGFloat = 92, gap: CGFloat = 70
var y: CGFloat = 512 + (3 * H + 2 * gap) / 2 - H
for (used, proj, tick, c) in meters {
  let track = NSBezierPath(roundedRect: NSRect(x: x0, y: y, width: W, height: H), xRadius: H/2, yRadius: H/2)
  NSColor.white.withAlphaComponent(0.08).setFill(); track.fill()
  c.withAlphaComponent(0.28).setFill()
  NSBezierPath(roundedRect: NSRect(x: x0, y: y, width: max(H, W*proj), height: H), xRadius: H/2, yRadius: H/2).fill()
  c.setFill()
  NSBezierPath(roundedRect: NSRect(x: x0, y: y, width: max(H, W*used), height: H), xRadius: H/2, yRadius: H/2).fill()
  NSColor.white.withAlphaComponent(0.92).setFill()
  NSBezierPath(roundedRect: NSRect(x: x0 + W*tick - 7, y: y - 18, width: 14, height: H + 36), xRadius: 7, yRadius: 7).fill()
  y -= H + gap
}
img.unlockFocus()
let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon_1024.png"
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print(out)
