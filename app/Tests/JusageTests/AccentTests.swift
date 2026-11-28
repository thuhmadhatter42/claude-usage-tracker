import AppKit
import SwiftUI
import XCTest
@testable import Jusage   // CHANGE: the app's module name

/// The accent colour. Lifted from Jlipper's AccentTests (itself modelled on Beat Key's); default is house amber.
@MainActor
final class AccentTests: XCTestCase {
    private var suite: String!
    private var defaults: UserDefaults!

    override func setUp() async throws {
        suite = "AccentTests.\(UUID().uuidString)"
        defaults = TestDefaults.make(suite)
    }

    override func tearDown() async throws {
        defaults.removePersistentDomain(forName: suite)
    }

    func testAmberIsTheDefault() {
        let a = AccentSettings(defaults: defaults)
        XCTAssertEqual(a.picked, .amber)
        XCTAssertEqual(a.effective, .amber)
        XCTAssertFalse(a.clamped)
        XCTAssertEqual(Accent.amber, Accent(255, 176, 32))
        XCTAssertEqual(Accent.amber.hex, "#ffb020", "the house amber (Artemis), J 2026-11-18")
        XCTAssertNil(defaults.object(forKey: AccentSettings.key), "nothing saved until the user picks")
    }

    func testPersistenceRoundTrip() {
        let a = AccentSettings(defaults: defaults)
        let sky = Accent(80, 170, 255)
        a.set(sky)
        XCTAssertEqual(defaults.array(forKey: AccentSettings.key) as? [Double], [80, 170, 255])
        XCTAssertEqual(a.effective, sky)
        let next = AccentSettings(defaults: defaults)      // the next launch
        XCTAssertEqual(next.picked, sky)
        XCTAssertEqual(next.effective, sky)
    }

    func testUnreadableStoredValueFallsBackToAmber() {
        defaults.set("purple", forKey: AccentSettings.key)
        XCTAssertEqual(AccentSettings(defaults: defaults).picked, .amber)
        defaults.set([1.0, 2.0], forKey: AccentSettings.key)
        XCTAssertEqual(AccentSettings(defaults: defaults).picked, .amber)
        defaults.set([Double.nan, 2.0, 3.0], forKey: AccentSettings.key)
        XCTAssertEqual(AccentSettings(defaults: defaults).picked, .amber)
    }

    func testReset() {
        let a = AccentSettings(defaults: defaults)
        a.set(Accent(60, 220, 120))
        a.reset()
        XCTAssertEqual(a.picked, .amber)
        XCTAssertEqual(a.effective, .amber)
        XCTAssertNil(defaults.object(forKey: AccentSettings.key))
        XCTAssertEqual(AccentSettings(defaults: defaults).picked, .amber)
    }

    func testComponentsAreClamped() {
        let a = Accent(-20, 300, 128)
        XCTAssertEqual(a.r, 0); XCTAssertEqual(a.g, 255); XCTAssertEqual(a.b, 128)
    }

    /// Text on an accent-filled button is black or white, whichever reads better, and reads at least 4.5:1 for
    /// every preset.
    func testInkIsBlackOrWhiteAndReadableOnEveryPreset() {
        for (name, p) in Accent.presets {
            XCTAssertTrue(p.ink == .black || p.ink == .white, name)
            XCTAssertGreaterThanOrEqual(p.contrast(with: p.ink), 4.5, "\(name) \(p.described)")
            let other: Accent = p.ink == .black ? .white : .black
            XCTAssertGreaterThanOrEqual(p.contrast(with: p.ink), p.contrast(with: other), "\(name): the better of the two")
        }
        XCTAssertEqual(Accent(255, 255, 120).ink, .black)
        XCTAssertEqual(Accent(20, 20, 140).ink, .white)
        XCTAssertEqual(Accent(90, 0, 40).ink, .white)
    }

    func testEveryPresetReadsOnTheBaseUnchanged() {
        XCTAssertEqual(Set(Accent.presets.map(\.name)).count, Accent.presets.count, "preset names unique")
        XCTAssertEqual(Set(Accent.presets.map(\.accent)).count, Accent.presets.count, "preset colours unique")
        XCTAssertEqual(Accent.presets.first?.accent, .amber, "amber is the first swatch")
        for (name, p) in Accent.presets {
            XCTAssertFalse(p.readable().clamped, name)
            XCTAssertGreaterThanOrEqual(p.contrast(with: .base), Accent.minContrast, name)
        }
    }

    func testDarkPickIsLiftedToReadOnTheBase() {
        let a = AccentSettings(defaults: defaults)
        let navy = Accent(10, 20, 90)
        a.set(navy)
        XCTAssertTrue(a.clamped)
        XCTAssertEqual(a.picked, navy, "the wheel keeps what the user picked")
        XCTAssertEqual(defaults.array(forKey: AccentSettings.key) as? [Double], [10, 20, 90], "the pick is saved, not the lift")
        XCTAssertGreaterThanOrEqual(a.effective.contrast(with: .base), Accent.minContrast - 0.01)
        XCTAssertLessThan(a.effective.contrast(with: .base), Accent.minContrast + 0.2, "lifted just enough")
        XCTAssertEqual(a.effective.hsb.0, navy.hsb.0, accuracy: 0.01, "hue survives the lift")
        // pure blue cannot get there on brightness alone: it goes paler instead
        let (b, clamped) = Accent(0, 0, 255).readable()
        XCTAssertTrue(clamped)
        XCTAssertGreaterThanOrEqual(b.contrast(with: .base), Accent.minContrast - 0.01)
        // black lifts too (no hue: a grey)
        XCTAssertGreaterThanOrEqual(Accent(0, 0, 0).readable().accent.contrast(with: .base), Accent.minContrast - 0.01)
        a.set(.amber)
        XCTAssertFalse(a.clamped)
    }

    func testDragIsCoalescedAndTheLastPickLands() {
        let a = AccentSettings(defaults: defaults)
        a.set(.amber)                              // just applied: the next drags wait their turn
        a.drag(Accent(255, 0, 0))
        a.drag(Accent(0, 255, 0))
        a.drag(Accent(120, 200, 255))
        let end = Date().addingTimeInterval(0.3)
        while Date() < end { RunLoop.main.run(until: Date().addingTimeInterval(0.01)) }
        XCTAssertEqual(a.picked, Accent(120, 200, 255))
        XCTAssertEqual(defaults.array(forKey: AccentSettings.key) as? [Double], [120, 200, 255])
    }

    func testPanelColourConvertsToSRGB() {
        XCTAssertNotNil(Accent(NSColor(calibratedRed: 0.2, green: 0.4, blue: 0.9, alpha: 1)))
        XCTAssertEqual(Accent(NSColor(srgbRed: 1, green: 176 / 255, blue: 32 / 255, alpha: 1)), .amber)
    }

    func testHighContrastIsRemembered() {
        let a = AccentSettings(defaults: defaults)
        XCTAssertFalse(a.highContrast, "off by default")
        a.highContrast = true
        XCTAssertTrue(defaults.bool(forKey: AccentSettings.highContrastKey))
        XCTAssertTrue(AccentSettings(defaults: defaults).highContrast, "the next launch")
    }

    func testTheEnvironmentDefaultIsAmber() {
        XCTAssertEqual(EnvironmentValues().accent, .amber)
        XCTAssertFalse(EnvironmentValues().highContrast)
    }
}
