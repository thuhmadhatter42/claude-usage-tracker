import AppKit
import Combine
import SwiftUI

/// S16: the app. Launch order (SCORE S16): house launch (AppLog launch line, tips, Doctor, uninstall prep) →
/// S9 bring-up → S5 settings → S14 permission → S12 status item → S13 panel → S11 observers → S10 start.
/// A bring-up refusal shows one plain line in the panel and the title's warning mark, and no engine run starts.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let args: LaunchArgs
    private let log = AppLog.shared
    private let model = AppModel()
    private var services: PanelServices!
    private var quickKeysWatch: AnyCancellable?
    private var settings: SettingsStore!
    private var engine: Engine?
    private var refusal: Refusal?
    private var notifier: Notifier?
    private var updater: AppUpdater!
    private var statusItem: StatusItemController!
    private var panelWindow: Panel!
    private var panel: PanelController!
    private var observers: Observers!
    private var scheduler: Scheduler?
    private var loginItem: LoginItem!
    private var subscriptions: Set<AnyCancellable> = []
    private var migrationTried = false

    init(args: LaunchArgs) { self.args = args }

    func applicationDidFinishLaunching(_ notification: Notification) {
        JusageHouse.launch(engineDoctor: { [weak self] in try self?.engineDoctor() ?? [] },
                           statusLineOff: { [weak self] in self?.statusLineOff() ?? (1, "jusage is quitting") })
        for p in args.problems { log.warn("launch", p) }

        settings = SettingsStore()
        bringUp()

        if engine != nil {
            let n = Notifier(center: SystemNotifications(), log: log)
            n.requestPermission()
            notifier = n
        }

        updater = AppUpdater()
        statusItem = StatusItemController(
            maker: SystemStatusSlots(),
            menuItems: { [unowned self] in JusageHouseMenu.items(updater: self.updater, quit: { NSApp.terminate(nil) }, presentSheet: self.presentSheet) },
            leftClick: { [unowned self] in self.panel.toggle() },
            log: log)

        loginItem = LoginItem(log: log)
        services = makeServices()
        let root = AnyView(ContentView(model: model, settings: settings, services: services).accentRoot())
        panelWindow = Panel(root: root, sheetHost: JusageHouse.sheetHost())
        panel = PanelController(window: panelWindow,
                                anchor: { [unowned self] in self.statusItem.anchor },
                                recoverAnchor: { [unowned self] in self.statusItem.ensureOnScreen() },
                                activate: { NSApp.activate(ignoringOtherApps: true) },
                                onShow: { [unowned self] in self.scheduler?.request(.panelOpen) },
                                log: log)
        panelWindow.onResignKey = { [unowned self] in self.panel.didResignKey() }

        observers = Observers(workspace: NSWorkspace.shared.notificationCenter, app: .default,
                              request: { [unowned self] t in self.scheduler?.request(t) },
                              layoutChanged: { [unowned self] in
                                  self.statusItem.ensureOnScreen()
                                  self.panel.replaceIfShown()
                              })
        observers.start()

        model.objectWillChange.merge(with: settings.objectWillChange)
            .receive(on: RunLoop.main)
            .sink { [unowned self] _ in self.redraw() }
            .store(in: &subscriptions)
        redraw()

        if let engine {
            let s = Scheduler(run: { [unowned self] force, done in self.runEngine(engine, force: force, done: done) },
                              onTick: { [unowned self] now in self.model.reevaluateStaleness(now: now) })
            scheduler = s
            s.start()
        }

        updater.checkInBackgroundAtLaunch()
        if args.checkForUpdates { updater.checkForUpdates() }
    }

    /// The kit's sheets hang off the panel window. A right-click menu item shows the panel (and activates the app)
    /// first, so the sheet always has a visible window to open on; from the panel's own menu it is already up.
    private func presentSheet(_ open: @escaping @MainActor () -> Void) {
        if !panel.isShown { panel.show() }
        open()
    }

    // MARK: bring-up

    private func bringUp() {
        if let p = args.setPython, let r = BringUp.setPython(p, settings: settings) {
            refuse(r); return
        }
        switch BringUp.check(bundle: .main, settings: settings) {
        case .failure(let r):
            refuse(r)
        case .success(let e):
            engine = e
            log.info("bringup", "engine \(e.version), python \(e.python.path)" + (e.stateDir.map { ", state \($0)" } ?? ""))
            if e.pythonRecordedNow || args.setPython != nil { recordPythonInEngine(e) }
        }
    }

    private func refuse(_ r: Refusal) {
        refusal = r
        HostFailure.refuse(r, log: log, model: model)
    }

    /// The `jusage` command runs the same Python as the app (SCORE §1.5): the engine's `config python`.
    private func recordPythonInEngine(_ e: Engine) {
        Task { @MainActor in
            let r = await FeedRunner.run(python: e.python, script: e.script, args: ["config", "python", e.python.path], stateDir: e.stateDir)
            if case .ok = r { log.info("bringup", "recorded python \(e.python.path) in the engine's config") }
            else { log.error("bringup", "could not record python in the engine's config: \(Self.describe(r))") }
        }
    }

    // MARK: engine runs

    private func runEngine(_ e: Engine, force: Bool, done: @escaping (Date?) -> Void) {
        model.runStarted()
        Task { @MainActor in
            let r = await FeedRunner.run(python: e.python, script: e.script, args: ["menu-json"] + (force ? ["--force"] : []), stateDir: e.stateDir)
            let now = Date()
            model.apply(r, now: now)
            var nextDue: Date? = nil
            if HostFailure.ranOK(r, model: model, log: log), let feed = model.feed {
                nextDue = feed.nextDue
                afterGoodFeed(feed, now: now)
            }
            done(nextDue)
        }
    }

    private func afterGoodFeed(_ feed: Feed, now: Date) {
        if !migrationTried {
            migrationTried = true
            settings.migrateFrom01(defaults: .standard, feed: feed, log: { [log] in log.info("settings", $0) })
        }
        let (notes, latch) = NotifyLatch.evaluate(feed: feed, settings: settings.settings,
                                                  latch: settings.settings.notificationLatch, now: now)
        let old = settings.settings.notificationLatch
        guard let notifier else { return }   // no engine, no notifier: nothing was delivered, nothing is recorded
        // A crossing is recorded as sent only once the system accepted its notification.
        notifier.post(notes) { [weak self] delivered in
            Task { @MainActor in
                guard let self else { return }
                let kept = NotifyLatch.committed(old: old, new: latch, notes: notes, delivered: delivered)
                if kept != self.settings.settings.notificationLatch { self.settings.update { $0.notificationLatch = kept } }
            }
        }
    }

    private func runSync(_ args: [String], timeout: TimeInterval) -> RunResult {
        guard let e = engine else { return .failed(line: refusal?.line ?? "jusage: the engine is not ready", exit: 2) }
        final class Box: @unchecked Sendable { var value: RunResult = .launchFailed("no answer") }
        let box = Box(), done = DispatchSemaphore(value: 0)
        Task.detached {
            box.value = await FeedRunner.run(python: e.python, script: e.script, args: args, stateDir: e.stateDir, timeout: timeout, killGrace: 2)
            done.signal()
        }
        done.wait()
        return box.value
    }

    private func engineDoctor() throws -> [String] {
        guard let e = engine else { throw refusal ?? Refusal(line: "jusage: the engine is not ready") }
        return try JusageDoctor.engineRunner(python: e.python, script: e.script, stateDir: e.stateDir)()
    }

    /// Uninstall's first step: the engine restores every status-line original (`statusline off --all`).
    private func statusLineOff() -> (Int32, String) {
        switch runSync(["statusline", "off", "--all"], timeout: 30) {
        case .ok(let data): return (0, String(decoding: data, as: UTF8.self))
        case .failed(let line, let code): return (code, line)
        case let other: return (1, Self.describe(other))
        }
    }

    // MARK: screen

    /// What W3-B's views get from the host (`PanelServices`): the same actions the status item and Quick Keys use.
    private func makeServices() -> PanelServices {
        let qk = QuickKeys.shared
        func shortcuts() -> [String: KeyboardShortcut] {
            var out: [String: KeyboardShortcut] = [:]
            for d in QuickKeys.appRows + QuickKeys.standardRows { out[d.id] = qk.shortcut(for: d.id) }
            return out
        }
        let s = PanelServices(
            shortcuts: shortcuts(),
            log: log,
            refresh: { [unowned self] in self.scheduler?.request(.manual) },
            openDashboard: { [unowned self] path in
                if !NSWorkspace.shared.open(URL(fileURLWithPath: path)) { self.log.error("dashboard", "could not open \(path)") }
            },
            houseMenu: StatusItemController.panelMenu(from: JusageHouseMenu.items(updater: updater, quit: { NSApp.terminate(nil) }, presentSheet: { [unowned self] in self.presentSheet($0) })),
            engine: { [unowned self] args in await self.engineCommand(args) },
            loginItem: LoginItemControl(isOn: { [unowned self] in self.loginItem.isOn },
                                        set: { [unowned self] on in self.loginItem.set(on) }),
            accentPicker: { AnyView(AccentPickerRow()) },
            glow: { view, lit in AnyView(view.accentGlow(HouseGlow.Full.litText, active: lit)) },
            quit: { NSApp.terminate(nil) },
            foldAnimationDone: { [unowned self] in self.panel.replaceIfShown() })
        quickKeysWatch = qk.objectWillChange.receive(on: RunLoop.main).sink { [weak s] _ in s?.shortcuts = shortcuts() }
        return s
    }

    /// An engine settings command from the panel (`config read-every 120`, `statusline on --folder K`). Without
    /// a good bring-up the refusal line comes back, never a silent no-op.
    private func engineCommand(_ args: [String]) async -> RunResult {
        guard let e = engine else { return .failed(line: refusal?.line ?? "jusage: the engine is not ready", exit: 2) }
        let r = await FeedRunner.run(python: e.python, script: e.script, args: args, stateDir: e.stateDir)
        if case .ok = r { scheduler?.request(.manual) }
        else { log.error("engine", "\(args.joined(separator: " ")) failed: \(Self.describe(r))") }
        return r
    }

    private func redraw() {
        let s = settings.settings
        statusItem.render(TitleModel.make(feed: model.feed, settings: s, now: model.clock, runError: model.runError))
        switch Theme.resolve(id: s.theme).scheme {
        case .dark?: NSApp.appearance = NSAppearance(named: .darkAqua)
        case .light?: NSApp.appearance = NSAppearance(named: .aqua)
        case nil: NSApp.appearance = nil
        }
    }

    /// For the log: the one failure text (AppModel, RULINGS 48e) plus the exit code where there is one.
    private static func describe(_ r: RunResult) -> String {
        guard let text = AppModel.failureText(r) else { return "ok" }
        if case .failed(_, let code) = r { return "\(text) (exit \(code))" }
        return text
    }
}

extension Refusal: LocalizedError {
    var errorDescription: String? { line }
}
