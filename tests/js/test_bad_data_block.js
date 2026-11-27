// Dashboard bring-up (SCORE §3): an unreadable data block, or one without rules.stale_after_s,
// paints one red line and nothing else.
function fakeDoc(dataText) {
  var main = {innerHTML: "<p>first paint</p>"};
  var data = dataText === null ? null : {textContent: dataText};
  return { main: main, getElementById: function (id) { return id === "data" ? data : null; },
           querySelector: function (s) { return s === "main" ? main : null; } };
}
var LINE = "Data block unreadable — rebuild with jusage dashboard";

test("broken JSON refuses", function () {
  assertThrows(function () { JDash.readData("{bad"); });
  var d = fakeDoc("{bad");
  assertEqual(JDash.boot(d), false);
  assert(d.main.innerHTML.indexOf(LINE) >= 0, d.main.innerHTML);
  assertEqual(d.main.innerHTML.indexOf("first paint"), -1);
});

test("missing stale_after_s refuses", function () {
  assertThrows(function () { JDash.readData('{"rules":{"red_at":97},"version":"0.2.0"}'); });
  var d = fakeDoc('{"rules":{},"version":"0.2.0"}');
  assertEqual(JDash.boot(d), false);
  assert(d.main.innerHTML.indexOf(LINE) >= 0);
});

test("missing data block refuses", function () {
  var d = fakeDoc(null);
  assertEqual(JDash.boot(d), false);
  assert(d.main.innerHTML.indexOf(LINE) >= 0);
});

test("a good block reads", function () {
  var data = JDash.readData('{"rules":{"stale_after_s":900},"version":"0.2.0","tips":{}}');
  assertEqual(data.rules.stale_after_s, 900);
});
