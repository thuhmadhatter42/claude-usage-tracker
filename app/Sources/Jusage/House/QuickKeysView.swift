import SwiftUI

/// The Quick Keys window: every shortcut by group; click a chip and press the new keys.
/// Shown as a sheet by `quickKeysSheet()`; opened by the ⇧⌃K menu item (`QuickKeysCommands`).
struct QuickKeysView: View {
    @ObservedObject var keys: QuickKeys
    @Environment(\.accent) private var theme

    /// `keys: nil` = the app's one table.
    @MainActor init(keys: QuickKeys? = nil) { self.keys = keys ?? .shared }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("QUICK KEYS").font(.system(size: 12, weight: .bold)).tracking(1.4)
                    .foregroundColor(theme.color).accentGlow(HouseGlow.Full.litText)
                Spacer()
                Button { keys.close() } label: { Image(systemName: "xmark").font(.system(size: 11, weight: .bold)) }
                    .buttonStyle(.plain).foregroundColor(.secondary)
                    .keyboardShortcut(.cancelAction)
                    .accessibilityLabel("Close")
            }
            .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 10)

            if let clash = keys.pendingClash {
                HStack(spacing: 10) {
                    Text(clash.reason).font(.callout).foregroundColor(theme.color)
                    Spacer()
                    Button("Cancel") { keys.cancelClash() }.buttonStyle(.crisp)
                    Button("Reassign") { keys.reassignClash() }.buttonStyle(.accent)
                }
                .padding(.horizontal, 16).padding(.vertical, 8)
                .background(theme.color(0.08))
                .accessibilityElement(children: .contain)
            } else if let message = keys.message {
                Text(message).font(.callout).foregroundColor(theme.color)
                    .padding(.horizontal, 16).padding(.vertical, 8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(theme.color(0.08))
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(keys.groups, id: \.name) { group in
                        Text(group.name.uppercased()).font(.system(size: 10, weight: .bold)).tracking(1.2)
                            .foregroundColor(.secondary)
                            .padding(.top, 12).padding(.bottom, 4)
                        ForEach(group.rows) { row in QuickKeyRowView(keys: keys, row: row) }
                    }
                }
                .padding(.horizontal, 16).padding(.bottom, 12)
            }

            Divider()
            HStack {
                Button("Reset All") { keys.resetAll() }.buttonStyle(.crisp)
                Spacer()
                Button("Done") { keys.close() }.buttonStyle(.accent).keyboardShortcut(.defaultAction)
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
        }
        .frame(width: 460, height: 520)
        .background(Color(nsColor: .windowBackgroundColor))
        .onDisappear { keys.stopListening() }
    }
}

private struct QuickKeyRowView: View {
    @ObservedObject var keys: QuickKeys
    let row: QuickKeyRow
    @Environment(\.accent) private var theme

    var body: some View {
        let listening = keys.listening == row.id
        let unassigned = !row.chord.isAssigned
        HStack(spacing: 8) {
            Text(row.def.title).font(.body).foregroundColor(.primary).lineLimit(1)
            Spacer(minLength: 12)
            Button {
                if listening { keys.stopListening() } else { keys.listen(row.id) }
            } label: {
                Text(listening ? "Press the new keys…" : row.chord.display)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundColor(row.locked ? Color.secondary : listening ? theme.ink.color : unassigned ? Color.secondary : theme.color)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .frame(minWidth: 84)
                    .background(RoundedRectangle(cornerRadius: 5).fill(listening ? theme.color : theme.color(row.locked ? 0 : 0.08)))
                    .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(row.locked ? Color.secondary.opacity(0.3) : theme.color(0.6), lineWidth: 1))
                    .accentGlow(HouseGlow.Full.litChip, active: listening)
            }
            .buttonStyle(.plain)
            .disabled(row.locked)
            .help(row.locked ? "This shortcut belongs to macOS" : "Click, then press the new keys")
            .accessibilityLabel("\(row.def.title) shortcut")
            .accessibilityValue(listening ? "Listening" : row.chord.display)

            Button { keys.clear(row.id) } label: { Image(systemName: "xmark.circle").font(.system(size: 12)) }
                .buttonStyle(.plain).foregroundColor(.secondary)
                .opacity(row.locked || unassigned ? 0 : 1)
                .disabled(row.locked || unassigned)
                .help("Leave this action without a shortcut")
                .accessibilityLabel("Clear \(row.def.title) shortcut")
        }
        .padding(.vertical, 3)
    }
}

/// A menu item (or any button) whose shortcut is its Quick Keys row: rebinding the row rebinds the item.
/// Use it for EVERY rebindable command instead of a literal `.keyboardShortcut("e", modifiers: ...)`.
struct QuickKeyButton: View {
    @ObservedObject private var keys: QuickKeys
    let id: String
    let title: String
    let action: () -> Void

    @MainActor init(_ id: String, _ title: String, keys: QuickKeys? = nil, action: @escaping () -> Void) {
        self.id = id; self.title = title; self.keys = keys ?? .shared; self.action = action
    }

    var body: some View {
        // While a chip listens the key monitor swallows every key press, so no menu item fires.
        // (Two branches, not `.keyboardShortcut(optional)`: that overload needs macOS 12.3.)
        if let shortcut = keys.shortcut(for: id) {
            Button(title, action: action).keyboardShortcut(shortcut)
        } else {
            Button(title, action: action)
        }
    }
}

/// The app-menu item: "Quick Keys" ⇧⌃K, right after Settings.
struct QuickKeysCommands: Commands {
    var body: some Commands {
        CommandGroup(after: .appSettings) {
            QuickKeyButton(QuickKeys.quickKeysID, "Quick Keys") { QuickKeys.shared.open() }
        }
    }
}

extension View {
    /// Put this on the main window's root view: the Quick Keys window is its sheet.
    @MainActor func quickKeysSheet(_ keys: QuickKeys? = nil) -> some View { modifier(QuickKeysSheet(keys: keys ?? .shared)) }
}

private struct QuickKeysSheet: ViewModifier {
    @ObservedObject var keys: QuickKeys
    func body(content: Content) -> some View {
        content.sheet(isPresented: $keys.windowOpen, onDismiss: { keys.close() }) {
            QuickKeysView(keys: keys).accentRoot()
        }
    }
}
