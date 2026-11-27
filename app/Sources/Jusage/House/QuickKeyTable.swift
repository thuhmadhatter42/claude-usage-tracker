import AppKit

// THE APP'S OWN SHORTCUTS. This is the per-app file of the Quick Keys module: one row per shortcut jusage has.
// The standard rows (Quick Keys ⇧⌃K, Settings ⌘, , Quit ⌘Q, Copy...) are added by QuickKeys.swift; do not
// repeat them here. The table is SPEC R-161: Refresh ⌘R, Dashboard ⌘D, Previous / Next month.
//
//   .menu  a menu command           needs Command, Control or Option
//   .key   a key the window handles needs at least one modifier
// House rule: a bare ← or → is refused by the kit (it would be typed into a text field), so the month keys are
// ⌥← and ⌥→, rebindable like every row.
// A menu item takes its chord from its row, never from a literal. QuickKeysTests fails if two rows share a chord.
extension QuickKeys {
    nonisolated static let appRows: [QuickKeyDef] = [
        QuickKeyDef("refresh", "Refresh", group: "Usage", key: "r", mods: .command),
        QuickKeyDef("dashboard", "Open Dashboard", group: "Usage", key: "d", mods: .command),
        QuickKeyDef("previousMonth", "Previous Month", group: "Month", kind: .key, key: "left", mods: .option),
        QuickKeyDef("nextMonth", "Next Month", group: "Month", kind: .key, key: "right", mods: .option),
    ]
}
