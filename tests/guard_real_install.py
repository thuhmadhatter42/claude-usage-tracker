"""RULING 54: no test may reach the real install.

tests/run_all.sh calls `snapshot <file>` before the suites and `compare <file>` after them. Every entry
under each guarded path (files, folders, links; lstat, never followed) is recorded with its type, size,
mode and mtime in nanoseconds, so a test that rewrites one file deep inside the app is caught even when
no folder mtime moves. A guarded path that is absent is recorded as absent; appearing later is a change.

Guarded paths: JUSAGE_GUARD_PATHS (colon-separated, for the harness's own tests), else
/Applications/jusage.app and ~/jusage, where ~ is the account's real home from the user database, never
$HOME (the suites run with temp homes).
"""
import json
import os
import pwd
import stat
import sys


def guarded_paths():
    env = os.environ.get("JUSAGE_GUARD_PATHS")
    if env:
        return [p for p in env.split(":") if p]
    home = pwd.getpwuid(os.getuid()).pw_dir
    return ["/Applications/jusage.app", os.path.join(home, "jusage")]


def entry(path):
    st = os.lstat(path)
    return [stat.S_IFMT(st.st_mode), st.st_size, stat.S_IMODE(st.st_mode), st.st_mtime_ns]


def _raise(err):
    raise err


def snapshot(roots):
    seen = {}
    for root in roots:
        if not os.path.lexists(root):
            seen[root] = None
            continue
        seen[root] = entry(root)
        if not stat.S_ISDIR(os.lstat(root).st_mode):
            continue
        for d, dirs, files in os.walk(root, onerror=_raise):
            for name in dirs + files:
                p = os.path.join(d, name)
                seen[p] = entry(p)
    return seen


def main(argv):
    if len(argv) != 3 or argv[1] not in ("snapshot", "compare"):
        print("usage: guard_real_install.py snapshot|compare <file>", file=sys.stderr)
        return 2
    if argv[1] == "snapshot":
        roots = guarded_paths()
        now = snapshot(roots)
        with open(argv[2], "w") as f:
            json.dump({"roots": roots, "entries": now}, f)
        for r in roots:
            print(f"guard: watching {r} ({'absent' if now[r] is None else sum(1 for p in now if p == r or p.startswith(r + os.sep))} entries)")
        return 0
    with open(argv[2]) as f:
        before = json.load(f)
    os.unlink(argv[2])
    now = snapshot(before["roots"])
    was = before["entries"]
    changed = sorted(p for p in set(was) | set(now) if was.get(p) != now.get(p))
    if not changed:
        print("guard: real install untouched")
        return 0
    print(f"guard: real install changed during the run ({len(changed)} entries):")
    for p in changed[:40]:
        how = "added" if p not in was or was[p] is None else "removed" if p not in now or now[p] is None else "changed"
        print(f"  {how}: {p}")
    if len(changed) > 40:
        print(f"  … and {len(changed) - 40} more")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
