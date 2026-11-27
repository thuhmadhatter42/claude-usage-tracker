// D7 house web modules, pasted from ~/dev/house-kit and owned here. Run under JavaScriptCore with a
// small stand-in for the browser: the logic of each module, as the page wires it.
var HOUSE_DIR = "engine/ju/dash/static/house/";

function houseSandbox() {
  var store = {};
  var ls = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; }
  };
  var props = {}, classes = {};
  var g = (0, eval)("this");
  g.window = g;
  g.localStorage = ls;
  g.navigator = {userAgent: "JSC test"};
  g.location = {pathname: "/dashboard.html", search: "", origin: "file://"};
  g.addEventListener = function () {};
  g.document = {
    readyState: "complete", visibilityState: "visible",
    documentElement: {
      style: {setProperty: function (k, v) { props[k] = v; }},
      classList: {toggle: function (c, on) { classes[c] = !!on; }}
    },
    getElementById: function (id) {
      return id === "data" ? {textContent: '{"version":"0.2.0","rules":{"stale_after_s":900},"tips":{}}'} : null;
    },
    querySelectorAll: function () { return []; },
    addEventListener: function () {}
  };
  delete g.HOUSE; delete g.clientErr;
  ["house-config.js", "errlog.js", "accent.js", "quickkeys.js", "report.js", "tips.js", "version.js"].forEach(function (f) {
    _load(ROOT + "/" + HOUSE_DIR + f);
  });
  return {store: store, props: props, classes: classes, reloadErrlog: function () { _load(ROOT + "/" + HOUSE_DIR + "errlog.js"); }};
}

test("config: jusage's values, version from the data block, report to the public address", function () {
  houseSandbox();
  assertEqual([HOUSE.appName, HOUSE.appId, HOUSE.version, HOUSE.reportTo, HOUSE.errUrl, HOUSE.reportUrl],
              ["jusage", "jusage", "0.2.0", "contact@fourletterrecords.com", "", ""]);
  assert(HouseVersion.valid(HOUSE.version));
});

test("errlog: a file:// page keeps its ring in localStorage across loads", function () {
  var sb = houseSandbox();
  clientErr.report("app", "first problem");
  assert(/first problem/.test(sb.store["jusage.errlog"] || ""), "ring saved");
  sb.reloadErrlog();
  assert(clientErr.recent().some(function (r) { return r.message === "first problem"; }), "ring read back");
  for (var i = 0; i < 70; i++) clientErr.report("app", "p" + i);
  assert(JSON.parse(sb.store["jusage.errlog"]).length <= 50, "ring capped at 50");
});

test("accent: amber by default, a pick is saved under the jusage key", function () {
  var sb = houseSandbox();
  HouseAccent.init();
  assertEqual(sb.props["--accent"], "#ffb020");
  HouseAccent.set([80, 170, 255]);
  assertEqual(sb.store["jusage.accentColor"], "[80,170,255]");
  HouseAccent.reset();
  assertEqual(sb.store["jusage.accentColor"], undefined);
});

test("quick keys: the page's rows (Customize, Esc) make a table with no problems", function () {
  var sb = houseSandbox();
  HouseQuickKeys.init({rows: JDash.quickKeyRows(HouseQuickKeys), storage: localStorage, noKeys: true});
  assertEqual(HouseQuickKeys.problems(), []);
  assertEqual(HouseQuickKeys.display("customize"), "⇧⌘,");
  assertEqual(HouseQuickKeys.display("closeDrawer"), "⎋");
  assertEqual(HouseQuickKeys.display("quickKeys"), "⌃⇧K");
  assertEqual(HouseQuickKeys.set("closeDrawer", "x", HouseQuickKeys.CMD).ok, false);
});

test("report: subject and mail go to the one address with the version", function () {
  houseSandbox();
  assertEqual(HouseReport.subject(), "jusage 0.2.0 report");
  assert(HouseReport.mailto("hi").indexOf("mailto:contact@fourletterrecords.com?subject=jusage%200.2.0%20report") === 0);
  clientErr.report("app", "for the log");
  assert(/for the log/.test(HouseReport.logText()));
});

test("tips: the hover the page script writes follows the rules", function () {
  houseSandbox();
  var now = Date.parse("2026-11-29T21:40:00-08:00");
  ["2026-11-29T21:20:00-08:00", "2026-11-26T08:05:00-08:00"].forEach(function (readAt) {
    var t = JDash.staleTitle(readAt, now, true, -480);
    assertEqual(HouseTips.check(t), [], t);
  });
  assert(HouseTips.check("Please simply click this").length > 0);
});
