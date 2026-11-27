import SwiftUI
import XCTest
@testable import Jusage   // CHANGE: the app's module name

final class GlowTests: XCTestCase {
    /// J 2026-11-18: opacity 12% of Artemis full, radius 6% (the opacity factor, halved). Two constants, not one.
    func testTheTwoHouseConstants() {
        XCTAssertEqual(HouseGlow.opacityScale, 0.12, accuracy: 0.0001)
        XCTAssertEqual(HouseGlow.radiusScale, 0.06, accuracy: 0.0001)
        XCTAssertEqual(Double(HouseGlow.radiusScale), HouseGlow.opacityScale / 2, accuracy: 0.0001, "radius is half the opacity factor")
    }

    func testScalingUsesEachConstantOnItsOwnAxis() {
        let s = HouseGlow.scaled(HouseGlow.Layer(radius: 10, opacity: 0.5))
        XCTAssertEqual(s.radius, 0.6, accuracy: 0.0001)
        XCTAssertEqual(s.opacity, 0.06, accuracy: 0.0001)
    }

    /// Every full-strength preset is Artemis' own (faceplate.css line in each comment; CSS blur = 2 x SwiftUI radius).
    func testArtemisFullPresets() {
        // :143 text-shadow 0 0 6px .6, 0 0 16px .4
        XCTAssertEqual(HouseGlow.Full.litText, [HouseGlow.Layer(radius: 3, opacity: 0.6), HouseGlow.Layer(radius: 8, opacity: 0.4)])
        // :302 box-shadow 0 0 3px 1px --amber-glow (.6)
        XCTAssertEqual(HouseGlow.Full.led, [HouseGlow.Layer(radius: 1.5, opacity: 0.6)])
        // :913 box-shadow 0 0 11px 2px --amber-glow (.6)
        XCTAssertEqual(HouseGlow.Full.litButton, [HouseGlow.Layer(radius: 5.5, opacity: 0.6)])
        // :528 box-shadow 0 0 5px 1px --amber-glow (.6)
        XCTAssertEqual(HouseGlow.Full.litChip, [HouseGlow.Layer(radius: 2.5, opacity: 0.6)])
    }

    func testTheScaledHaloIsTwelvePercentOpacityAndSixPercentRadius() {
        let s = HouseGlow.scaled(HouseGlow.Full.litButton[0])
        XCTAssertEqual(s.radius, 5.5 * 0.06, accuracy: 0.0001)
        XCTAssertEqual(s.opacity, 0.6 * 0.12, accuracy: 0.0001)
    }

    @MainActor
    func testTheModifiersAndStylesBuild() {
        // every call shape of the module, laid out in an off-screen hosting view (no window)
        let all = VStack {
            Text("120").accentGlow(HouseGlow.Full.litText)
            Text("off").accentGlow(active: false)
            Text("raw").accentGlow(radius: 5.5, opacity: 0.6)
            Button("Go") {}.buttonStyle(.accent)
            Button("More") {}.buttonStyle(.crisp)
        }
        let host = NSHostingView(rootView: all)
        host.layoutSubtreeIfNeeded()
        XCTAssertGreaterThan(host.fittingSize.height, 0)
    }
}
