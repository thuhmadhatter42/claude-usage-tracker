// D6 staleness (R-52, R-197): 14:59 old keeps its colour, 15:00 is grey, judged on the reader's clock.
var T0 = Date.parse("2026-11-26T21:00:00-08:00");
var READ = "2026-11-26T21:00:00-08:00";

test("899 s old is fresh", function () {
  var f = JDash.freshness(READ, T0 + 899 * 1000, 900);
  assertEqual(f.stale, false);
  assertEqual(f.ageS, 899);
});

test("900 s old is grey", function () {
  assertEqual(JDash.freshness(READ, T0 + 900 * 1000, 900).stale, true);
});

test("the window is the data block's, not a constant in the script", function () {
  assertEqual(JDash.freshness(READ, T0 + 120 * 1000, 60).stale, true);
  assertEqual(JDash.freshness(READ, T0 + 120 * 1000, 3600).stale, false);
});

test("the hover says how old and when", function () {
  var t = JDash.staleTitle(READ, T0 + 23 * 60 * 1000, false, -480);
  assertEqual(t, "Updated 23 min ago (21:00).");
});
