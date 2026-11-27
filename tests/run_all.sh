#!/bin/bash
# Every suite, one verdict. Ends with exactly "ALL PASS" (exit 0) or "FAIL" (exit 1).
#
# Suites (each is skipped only when its folder does not exist yet, so later waves are picked up
# without editing this file):
#   engine   python3 -m unittest discover -s tests/engine -t .
#   install  python3 -m unittest discover -s tests/install -t .   and every tests/install/test_*.sh
#   js       osascript -l JavaScript tests/js/run.js
#   swift    swift test, run only when app/Sources/Jusage holds a file besides .keep
#            (a Swift package with no sources cannot build); with sources present and no `swift`
#            on PATH the run FAILS, it is never skipped.
# A repo with no engine tests is a FAIL: "no tests" must never read as a pass.
# JUSAGE_ROOT overrides the repo root (the harness's own tests use it).
set -u
ROOT="${JUSAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PY="${PYTHON:-python3}"
cd "$ROOT" || { echo "cannot enter $ROOT"; echo FAIL; exit 1; }

failed=()
ran=0

suite() {   # suite <name> <command...>
    local name="$1"; shift
    echo "== $name =="
    ran=$((ran + 1))
    if "$@"; then echo "-- $name: pass"; else echo "-- $name: FAILED"; failed+=("$name"); fi
}

has_py_tests() { compgen -G "$1/test_*.py" > /dev/null; }

if has_py_tests tests/engine; then
    suite engine "$PY" -m unittest discover -s tests/engine -t . -v
else
    echo "== engine =="; echo "-- engine: no tests found in tests/engine"; failed+=("engine (no tests)")
fi

if [ -d tests/install ]; then
    if has_py_tests tests/install; then
        suite install-py "$PY" -m unittest discover -s tests/install -t . -v
    fi
    for t in tests/install/test_*.sh; do
        [ -e "$t" ] || continue
        suite "install-sh $(basename "$t")" bash "$t"
    done
fi

if [ -f tests/js/run.js ]; then
    suite js osascript -l JavaScript tests/js/run.js
fi

if [ -d app/Sources/Jusage ] && [ -n "$(find app/Sources/Jusage -type f ! -name .keep -print -quit)" ]; then
    if [ -d app/Sources/Jusage/Core ] && [ ! -f app/Sources/Jusage/Host/main.swift ]; then
        # RULINGS 25: the app target cannot link before Host/main.swift (wave 3). Removed at the wave-3 merge.
        echo "== swift =="; echo "-- swift: waiting for Host/main.swift (wave 3)"
    elif command -v swift > /dev/null 2>&1; then
        suite swift bash -c 'cd app && swift test --scratch-path "${TMPDIR:-/tmp}/jusage-swift"'
    else
        echo "== swift =="; echo "-- swift: swift not found, but app/Sources/Jusage has sources"; failed+=("swift (swift not found)")
    fi
else
    echo "== swift =="; echo "-- swift: skipped (app/Sources/Jusage has no sources beyond .keep)"
fi

echo
echo "suites run: $ran"
if [ "${#failed[@]}" -gt 0 ]; then
    for f in "${failed[@]}"; do echo "failed: $f"; done
    echo FAIL
    exit 1
fi
echo "ALL PASS"
