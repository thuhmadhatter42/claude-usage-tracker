// D6 formats: the one fixture table (tests/fixtures/formats.json), shared with Python (E19) and Swift (S2).
var FX = readJSON("tests/fixtures/formats.json");

test("tokens match the fixture", function () {
  FX.tokens.forEach(function (r) { assertEqual(JDash.fmtTokens(r.n), r.text, "n=" + r.n); });
});

test("dollars match the fixture", function () {
  FX.usd.forEach(function (r) { assertEqual(JDash.fmtUsd(r.usd), r.text, "usd=" + r.usd); });
});

test("countdowns match the fixture", function () {
  FX.countdown.forEach(function (r) { assertEqual(JDash.fmtCountdown(r.seconds), r.text, "s=" + r.seconds); });
});

test("clocks match the fixture (12/24 hour, same day vs weekday)", function () {
  FX.clock.forEach(function (r) {
    var got = JDash.fmtClock(Date.parse(r.at), Date.parse(r.now), r.hour12, r.tz_offset_min);
    assertEqual(got, r.text, r.at + " hour12=" + r.hour12);
  });
});

test("JS reads ISO with offsets only; a naive time is refused", function () {
  assertEqual(JDash.parseIso("2026-11-26T14:05:00-08:00"), Date.parse("2026-11-26T22:05:00Z"));
  assertThrows(function () { JDash.parseIso("2026-11-26T14:05:00"); });
  assertThrows(function () { JDash.parseIso("soon"); });
});
