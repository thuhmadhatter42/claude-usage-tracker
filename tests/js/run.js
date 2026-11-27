// JS test runner (T1). Run from anywhere:   osascript -l JavaScript tests/js/run.js
// Loads engine/ju/dash/static/dash.js into the global scope when it exists, then every
// tests/js/test_*.js, runs the tests they register, prints one line per test and ends with
// exactly "ALL PASS" (exit 0) or "FAIL" (exit 1). No node: JavaScriptCore via osascript.
//
// Test files use these globals:
//   test(name, fn)  assert(cond, msg)  assertEqual(actual, expected, msg)  assertThrows(fn)
//   readJSON(relPath)  readText(relPath)  ROOT
// Overrides (used by the harness's own tests): --dash <file>  --tests <dir>
ObjC.import('Foundation');
ObjC.import('stdlib');

var _fm = $.NSFileManager.defaultManager;
var _out = $.NSFileHandle.fileHandleWithStandardOutput;
function _write(s) { _out.writeData($.NSString.alloc.initWithUTF8String(s + "\n").dataUsingEncoding($.NSUTF8StringEncoding)); }

function _abs(p, base) { return p.charAt(0) === "/" ? p : base + "/" + p; }
function _dirname(p) { return p.replace(/\/[^\/]*$/, ""); }
function _exists(p) { return _fm.fileExistsAtPath($(p)); }
function _read(p) {
  var err = $();
  var s = $.NSString.stringWithContentsOfFileEncodingError($(p), $.NSUTF8StringEncoding, err);
  if (!s || !s.js && s.js !== "") throw new Error("cannot read " + p);
  return s.js;
}

var _cwd = ObjC.unwrap(_fm.currentDirectoryPath);
var _procArgs = ObjC.deepUnwrap($.NSProcessInfo.processInfo.arguments);
var _scriptArg = _procArgs.filter(function (a) { return /run\.js$/.test(a); })[0];
var ROOT = _dirname(_dirname(_dirname(_abs(_scriptArg, _cwd))));

var _tests = [];
var _current = "";
function test(name, fn) { _tests.push({file: _current, name: name, fn: fn}); }
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }
function assertEqual(actual, expected, msg) {
  var a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error((msg ? msg + ": " : "") + "expected " + e + ", got " + a);
}
function assertThrows(fn, msg) {
  var threw = false;
  try { fn(); } catch (e) { threw = true; }
  if (!threw) throw new Error(msg || "expected the function to throw");
}
function readText(rel) { return _read(_abs(rel, ROOT)); }
function readJSON(rel) { return JSON.parse(readText(rel)); }

var _global = (0, eval)("this");
function _load(path) { (0, eval)(_read(path) + "\n//# sourceURL=" + path); }

function run(argv) {
  var dash = ROOT + "/engine/ju/dash/static/dash.js";
  var testsDir = ROOT + "/tests/js";
  for (var i = 0; i < argv.length; i++) {
    if (argv[i] === "--dash") dash = _abs(argv[++i], _cwd);
    else if (argv[i] === "--tests") testsDir = _abs(argv[++i], _cwd);
    else { _write("unknown argument " + argv[i]); _write("FAIL"); $.exit(1); }
  }
  var failures = 0;

  if (_exists(dash)) {
    try { _load(dash); _write("loaded " + dash.replace(ROOT + "/", "")); }
    catch (e) { _write("FAIL cannot load " + dash.replace(ROOT + "/", "") + ": " + e); failures++; }
  } else {
    _write("note: " + dash.replace(ROOT + "/", "") + " not present yet");
  }

  var names = ObjC.deepUnwrap(_fm.contentsOfDirectoryAtPathError($(testsDir), $())) || [];
  names = names.filter(function (n) { return /^test_.*\.js$/.test(n); }).sort();
  names.forEach(function (n) {
    _current = n;
    try { _load(testsDir + "/" + n); }
    catch (e) { _write("FAIL " + n + " did not load: " + e); failures++; }
  });

  var passed = 0;
  _tests.forEach(function (t) {
    try { t.fn(); passed++; _write("ok   " + t.file + " :: " + t.name); }
    catch (e) { failures++; _write("FAIL " + t.file + " :: " + t.name + ": " + e.message); }
  });

  _write("js: " + passed + " passed, " + failures + " failed, " + _tests.length + " tests in " + names.length + " files");
  if (failures > 0) { _write("FAIL"); $.exit(1); }
  _write("ALL PASS");
  $.exit(0);
}
