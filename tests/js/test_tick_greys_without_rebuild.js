// D6 tick: greys and rewrites text in place on the reader's clock; never rebuilds the page.
function fakeEl(dataset, kids) {
  var cls = {};
  var el = {
    dataset: dataset || {}, textContent: "", title: "", innerHTML: "untouched", _kids: kids || {},
    classList: {
      add: function (c) { cls[c] = 1; }, remove: function (c) { delete cls[c]; },
      toggle: function (c, on) { if (on === undefined) on = !cls[c]; if (on) cls[c] = 1; else delete cls[c]; return on; },
      contains: function (c) { return !!cls[c]; }
    },
    querySelector: function (s) { return el._kids[s] || null; },
    setAttribute: function (k, v) { el[k] = v; }
  };
  return el;
}
function fakeRoot(lists) {
  return { querySelectorAll: function (s) { return lists[s] || []; } };
}
var RULES = {stale_after_s: 900};
var NOW = Date.parse("2026-11-26T21:40:00-08:00");

test("a meter older than 15 min greys with no engine run and no rebuild", function () {
  var m = fakeEl({readAt: "2026-11-26T21:20:00-08:00", reset: "2026-11-27T00:10:00-08:00", state: "green"},
                 {".rt": fakeEl()});
  var root = fakeRoot({"[data-read-at]": [m], ".meter[data-reset]": [m]});
  JDash.tick(root, NOW, RULES, false, -480);
  assert(m.classList.contains("stale"), "the meter greys");
  assertEqual(m.innerHTML, "untouched");
  assert(/^Updated 20 min ago/.test(m.title), m.title);
});

test("a fresh meter keeps its colour and its countdown runs on the reader's clock", function () {
  var rt = fakeEl();
  var m = fakeEl({readAt: "2026-11-26T21:39:00-08:00", reset: "2026-11-26T23:45:00-08:00", state: "green"},
                 {".rt": rt});
  JDash.tick(fakeRoot({"[data-read-at]": [m], ".meter[data-reset]": [m]}), NOW, RULES, false, -480);
  assert(!m.classList.contains("stale"));
  assertEqual(rt.textContent, "resets 23:45 · in 2h 05m");
});

test("a passed reset says reset due, never 0h 00m, and greys", function () {
  var rt = fakeEl();
  var m = fakeEl({readAt: "2026-11-26T21:39:00-08:00", reset: "2026-11-26T21:30:00-08:00", state: "amber"},
                 {".rt": rt});
  JDash.tick(fakeRoot({"[data-read-at]": [m], ".meter[data-reset]": [m]}), NOW, RULES, false, -480);
  assertEqual(rt.textContent, "Reset due, waiting for a fresh reading");
  assert(m.classList.contains("reset-due"));
});

test("runs-out words get the reader's clock", function () {
  var pw = fakeEl();
  var m = fakeEl({runsOut: "2026-11-26T23:10:00-08:00", words: "At this pace you hit the limit {runs_out}, 1h before the reset."},
                 {".pw": pw});
  JDash.tick(fakeRoot({"[data-runs-out]": [m]}), NOW, RULES, true, -480);
  assertEqual(pw.textContent, "At this pace you hit the limit 11:10 PM, 1h before the reset.");
});

test("clock labels follow the 12/24-hour setting", function () {
  var t = fakeEl({clock: "2026-11-26T20:40:00-08:00"});
  JDash.tick(fakeRoot({"time[data-clock]": [t]}), NOW, RULES, true, -480);
  assertEqual(t.textContent, "8:40 PM");
});
