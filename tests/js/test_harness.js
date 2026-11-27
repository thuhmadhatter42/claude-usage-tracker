// Harness self-test: the helpers every dashboard test relies on.
test("assertEqual compares deep values", function () {
  assertEqual({a: [1, 2, {b: "x"}]}, {a: [1, 2, {b: "x"}]});
  assertThrows(function () { assertEqual([1, 2], [1, 3]); });
});

test("assertThrows fails when nothing throws", function () {
  var threw = false;
  try { assertThrows(function () {}); } catch (e) { threw = true; }
  assert(threw, "assertThrows must throw when the function does not");
});

test("readJSON loads a fixture relative to the repo root", function () {
  var f = readJSON("tests/fixtures/formats.json");
  assert(f.tokens.length >= 12, "formats fixture has token cases");
  assertEqual(f.tokens[0], {n: 0, text: "0"});
});

test("the pace fixture rows are loadable and carry expectations", function () {
  var f = readJSON("tests/fixtures/pace_rows.json");
  assert(f.rows.length >= 24);
  assert(f.rows.every(function (r) { return r.expect && typeof r.expect.forecast === "boolean"; }));
});

test("the golden feed decodes and is schema 2", function () {
  var feed = readJSON("tests/fixtures/feed-v2.json");
  assertEqual(feed.schema, 2);
  assertEqual(feed.rules.stale_after_s, 900);
});
