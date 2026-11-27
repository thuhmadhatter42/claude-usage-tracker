import SwiftUI

/// S17: the panel's content (R-65, R-66, R-169). Header: Today/Month tabs (or "jusage" with no months), the
/// spinner or the last successful refresh time, a warning mark after a failed run, refresh / dashboard /
/// settings buttons bound to their Quick Keys rows, and the "⋯" house menu. Below: the chosen tab.
/// The host owns the panel's height and scrolling (S13); this view lays out at its natural height.
enum HeaderTrailing: Equatable {
    case none
    case spinner
    case time(String)
}

struct HeaderModel: Equatable {
    let tabs: Bool
    let trailing: HeaderTrailing
    /// The failed run's reason, shown on hover of the warning mark.
    let warning: String?
    let dashboard: Bool

    @MainActor
    static func make(model: AppModel, time: DisplayTime) -> HeaderModel {
        let trailing: HeaderTrailing
        if model.running { trailing = .spinner }
        else if let at = model.lastSuccessAt { trailing = .time(time.clock(at)) }
        else { trailing = .none }
        return HeaderModel(tabs: !(model.feed?.months.isEmpty ?? true), trailing: trailing, warning: model.runError,
                           dashboard: model.feed?.dashboard != nil)
    }
}

/// Which tab shows, and where leaving Settings goes (R-66: back to the tab you came from).
struct TabNav: Equatable {
    private(set) var cameFrom: Tab = .today

    static func shown(_ tab: Tab, hasMonths: Bool) -> Tab { tab == .month && !hasMonths ? .today : tab }

    mutating func toggleSettings(_ tab: inout Tab) {
        if tab == .settings { tab = cameFrom } else { cameFrom = tab; tab = .settings }
    }

    mutating func select(_ t: Tab, _ tab: inout Tab) {
        if t != .settings { cameFrom = t }
        tab = t
    }
}

/// View-level failures to the house log, once per change: a run or a feed that failed, error kinds this app does
/// not know, peer reports that did not decode.
enum PanelFailureLog {
    static func runError(from old: String?, to new: String?, log: HouseLog) {
        if let new, new != old { log.error("feed", new) }
    }

    static func feedArrived(_ feed: Feed, log: HouseLog) {
        for kind in BannerText.unknownKinds(in: feed) { log.error("feed", "unknown error kind \(kind)") }
        if feed.droppedPeople > 0 { log.error("feed", "\(feed.droppedPeople) peer report(s) did not decode and were dropped") }
    }
}

enum TodayText {
    static let waiting = "Waiting for the first reading."
}

struct ContentView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var settings: SettingsStore
    @ObservedObject var services: PanelServices
    @StateObject private var engine: EngineSettingsWriter
    @StateObject private var login: LoginItemWriter
    @State private var nav = TabNav()

    init(model: AppModel, settings: SettingsStore, services: PanelServices) {
        self.model = model
        self.settings = settings
        self.services = services
        _engine = StateObject(wrappedValue: EngineSettingsWriter(run: services.engine, log: services.log, settings: settings))
        _login = StateObject(wrappedValue: LoginItemWriter(control: services.loginItem, log: services.log))
    }

    var body: some View {
        let time = DisplayTime(now: model.clock)
        let palette = Palette(settings: settings.settings)
        let header = HeaderModel.make(model: model, time: time)
        VStack(alignment: .leading, spacing: Layout.blockGap) {
            headerRow(header, palette: palette)
            switch TabNav.shown(model.tab, hasMonths: header.tabs) {
            case .today:
                today(time: time, palette: palette)
            case .month:
                if let feed = model.feed { MonthView(feed: feed, index: $model.monthIndex, palette: palette) }
            case .settings:
                SettingsView(feed: model.feed, store: settings, engine: engine, login: login, palette: palette)
            }
        }
        .padding(Layout.padding)
        .frame(width: Layout.width, alignment: .topLeading)
        .background(palette.colour("bg"))
        .environmentObject(services)
        .onChange(of: model.runError) { [old = model.runError] new in
            PanelFailureLog.runError(from: old, to: new, log: services.log)
        }
        .onChange(of: model.feed?.generated) { _ in
            guard let feed = model.feed else { return }
            PanelFailureLog.feedArrived(feed, log: services.log)
            engine.feedArrived()
        }
    }

    @ViewBuilder private func today(time: DisplayTime, palette: Palette) -> some View {
        let banners = BannerText.banners(feed: model.feed, runError: model.runError, settings: settings.settings, time: time)
        if let feed = model.feed {
            TodayView(feed: feed, banners: banners, settings: settings.settings, time: time, palette: palette,
                      setFold: { id, open in settings.update { $0.setFold(id, open: open) } })
        } else {
            VStack(alignment: .leading, spacing: Layout.blockGap) {
                ForEach(banners) { ErrorBanner(banner: $0, palette: palette) }
                Text(TodayText.waiting).font(Layout.small).foregroundColor(palette.muted)
            }
        }
    }

    private func headerRow(_ h: HeaderModel, palette: Palette) -> some View {
        HStack(spacing: 8) {
            if h.tabs {
                tabButton("Today", .today, palette: palette)
                tabButton("Month", .month, palette: palette)
            } else {
                Text("jusage").font(Layout.body.weight(.semibold)).foregroundColor(palette.ink)
            }
            Spacer(minLength: 4)
            switch h.trailing {
            case .spinner: ProgressView().controlSize(.small).scaleEffect(0.7).frame(width: 16, height: 16)
            case .time(let t): Text(t).font(Layout.small.monospacedDigit()).foregroundColor(palette.muted)
            case .none: EmptyView()
            }
            if let w = h.warning {
                Text(TitleModel.warning).font(Layout.small).foregroundColor(palette.state(.amber)).help(w)
            }
            Group {
                services.glow(AnyView(
                    Button(action: services.refresh) { Image(systemName: "arrow.clockwise") }
                        .keyboardShortcut(services.shortcut(QuickKeyID.refresh))
                        .help("Refresh")
                ), model.running)
                if h.dashboard, let path = model.feed?.dashboard {
                    Button { services.openDashboard(path) } label: { Image(systemName: "chart.bar") }
                        .keyboardShortcut(services.shortcut(QuickKeyID.dashboard))
                        .help("Dashboard")
                }
                Button { nav.toggleSettings(&model.tab) } label: {
                    Image(systemName: model.tab == .settings ? "gearshape.fill" : "gearshape")
                }
                .keyboardShortcut(services.shortcut(QuickKeyID.settings))
                .help("Settings")
                Menu {
                    ForEach(services.houseMenu) { item in
                        if item.dividerBefore { Divider() }
                        Button(item.title, action: item.action)
                    }
                } label: { Image(systemName: "ellipsis") }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
            }
            .buttonStyle(.plain)
            .foregroundColor(palette.muted)
        }
    }

    private func tabButton(_ title: String, _ t: Tab, palette: Palette) -> some View {
        let selected = TabNav.shown(model.tab, hasMonths: true) == t
        return Button { nav.select(t, &model.tab) } label: {
            Text(title).font(Layout.body.weight(selected ? .semibold : .regular))
                .foregroundColor(selected ? Color.accentColor : palette.muted)
        }
        .buttonStyle(.plain)
    }
}
