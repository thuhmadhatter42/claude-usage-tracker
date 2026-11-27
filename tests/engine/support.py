"""T1 harness: the helpers every engine test builds on.

    support.temp_home()                      temp HOME + state dir (HOME restored on cleanup)
    support.fake_security(items, hang=False) fake `security` first on PATH
    support.FakeUsageServer(responses)       the usage endpoint on 127.0.0.1
    support.write_transcript(folder, rows)   a Claude Code transcript with token rows only

Importing the engine in tests: `from ju import constants` (tests/engine/__init__.py puts the
`engine/` folder on sys.path, exactly as claude_usage.py does in production).
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(name: str):
    """A JSON file from tests/fixtures/."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- temp HOME

class TempHome:
    """A throwaway HOME with an empty 0700 `.claude-usage` state dir. HOME is set at creation
    and restored by cleanup() (or leaving a `with`)."""

    def __init__(self):
        self.home = Path(tempfile.mkdtemp(prefix="jusage-home-")).resolve()
        self.state = self.home / ".claude-usage"
        self.state.mkdir(mode=0o700)
        os.chmod(self.state, 0o700)
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self._done = False

    def cleanup(self):
        if self._done:
            return
        self._done = True
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        shutil.rmtree(self.home, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()


def temp_home() -> TempHome:
    return TempHome()


# ---------------------------------------------------------------- fake `security`

_FAKE_SECURITY = """#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
def record(refused):
    with open(os.path.join(here, "calls.log"), "a") as f:
        f.write(json.dumps({{"argv": argv, "refused": refused}}) + "\\n")
ok = bool(argv) and argv[0] == "find-generic-password" and "-w" in argv and "-s" in argv and argv.index("-s") + 1 < len(argv)
if not ok:
    record(True)
    sys.stderr.write("fake security: only `find-generic-password -s <service> -w` is allowed\\n")
    sys.exit(64)
record(False)
if os.path.exists(os.path.join(here, "hang")):
    time.sleep(600)
items = json.load(open(os.path.join(here, "items.json")))
service = argv[argv.index("-s") + 1]
if service in items:
    sys.stdout.write(items[service] + "\\n")
    sys.exit(0)
sys.stderr.write("security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.\\n")
sys.exit(44)
"""


class FakeSecurity:
    """A `security` executable that answers `find-generic-password -s <service> -w` from `items`
    and refuses (exit 64, recorded) everything else, so a test proves the engine never writes.
    `hang=True` makes every call block until killed (the Keychain prompt case)."""

    def __init__(self, items: dict, hang: bool = False):
        self.bin_dir = Path(tempfile.mkdtemp(prefix="jusage-fakesec-")).resolve()
        (self.bin_dir / "items.json").write_text(json.dumps(items))
        if hang:
            (self.bin_dir / "hang").write_text("")
        exe = self.bin_dir / "security"
        exe.write_text(_FAKE_SECURITY.format(python=sys.executable))
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self._orig_path = os.environ.get("PATH", "")
        self._active = False

    def path(self) -> str:
        """PATH with the fake first, built from the PATH seen at creation."""
        return str(self.bin_dir) + os.pathsep + self._orig_path

    def activate(self):
        os.environ["PATH"] = self.path()
        self._active = True

    def _log(self):
        f = self.bin_dir / "calls.log"
        if not f.exists():
            return []
        return [json.loads(l) for l in f.read_text().splitlines() if l]

    def calls(self) -> list:
        """argv (without the program name) of every call that was allowed, in order."""
        return [c["argv"] for c in self._log() if not c["refused"]]

    def refused(self) -> list:
        return [c["argv"] for c in self._log() if c["refused"]]

    def services_asked(self) -> list:
        return [a[a.index("-s") + 1] for a in self.calls()]

    def cleanup(self):
        if self._active:
            os.environ["PATH"] = self._orig_path
            self._active = False
        shutil.rmtree(self.bin_dir, ignore_errors=True)

    def __enter__(self):
        self.activate()
        return self

    def __exit__(self, *exc):
        self.cleanup()


def fake_security(items: dict, hang: bool = False) -> FakeSecurity:
    return FakeSecurity(items, hang)


# ---------------------------------------------------------------- fake usage server

Request = collections.namedtuple("Request", "method path headers body")


class FakeUsageServer:
    """The usage endpoint on 127.0.0.1. `responses` = [(status, headers, body)] served in order;
    body is bytes or a dict (sent as JSON). When the list runs out it answers 599
    "no response queued" (and still records the request), so a test sees an unexpected call."""

    def __init__(self, responses):
        self._queue = list(responses)
        self._lock = threading.Lock()
        self.requests: list = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _serve(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                with outer._lock:
                    outer.requests.append(Request(self.command, self.path, self.headers, body))
                    item = outer._queue.pop(0) if outer._queue else None
                if item is None:
                    status, headers, payload = 599, {}, b"no response queued"
                else:
                    status, headers, payload = item
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload).encode()
                headers = dict(headers)
                if not any(k.lower() == "content-type" for k in headers):
                    headers["Content-Type"] = "application/json"
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _serve

            def log_message(self, *a):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/api/oauth/usage"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------- transcripts

_TOKEN_KEYS = ("input", "output", "cache_5m", "cache_1h", "cache_read")
_counter = collections.Counter()


def _project_dir_name(cwd: str) -> str:
    return re.sub(r"[/.]", "-", cwd)


def write_transcript(folder, rows, session: str = "sess-1", cwd: str = "/work/demo") -> Path:
    """Append rows to `<folder>/projects/<encoded cwd>/<session>.jsonl` and return that file.

    A row is a dict, or a str written verbatim (garbage lines). Dict keys: ts (ISO with offset,
    required), model (required), session, cwd, input, output, cache_5m, cache_1h, cache_read
    (default 0), stop_reason (default "end_turn"; None writes null = an unfinished reply),
    message_id, request_id, uuid (defaults generated; None omits the key). Only token counts are
    written, never message text. All dict rows must resolve to one session and cwd."""
    folder = Path(folder)
    dict_rows = [r for r in rows if isinstance(r, dict)]
    targets = {(r.get("session", session), r.get("cwd", cwd)) for r in dict_rows} or {(session, cwd)}
    if len(targets) != 1:
        raise ValueError("write_transcript: rows span several sessions or folders; call once per file")
    (sess, wd), = targets
    path = folder / "projects" / _project_dir_name(wd) / f"{sess}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for r in rows:
        if isinstance(r, str):
            lines.append(r)
            continue
        n = _counter[str(path)] = _counter[str(path)] + 1
        u = {k: r.get(k, 0) for k in _TOKEN_KEYS}
        o = {
            "type": "assistant",
            "timestamp": r["ts"],
            "sessionId": sess,
            "cwd": wd,
            "message": {
                "model": r["model"],
                "stop_reason": r.get("stop_reason", "end_turn"),
                "usage": {
                    "input_tokens": u["input"],
                    "output_tokens": u["output"],
                    "cache_read_input_tokens": u["cache_read"],
                    "cache_creation_input_tokens": u["cache_5m"] + u["cache_1h"],
                    "cache_creation": {"ephemeral_5m_input_tokens": u["cache_5m"],
                                       "ephemeral_1h_input_tokens": u["cache_1h"]},
                },
            },
        }
        for key, default in (("message_id", f"msg_{sess}_{n}"), ("request_id", f"req_{sess}_{n}"),
                             ("uuid", f"uuid_{sess}_{n}")):
            v = r.get(key, default)
            if v is None:
                continue
            if key == "message_id":
                o["message"]["id"] = v
            elif key == "request_id":
                o["requestId"] = v
            else:
                o["uuid"] = v
        lines.append(json.dumps(o))
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    return path
