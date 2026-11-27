"""E8: transcripts -> usage.db. Reads token counts only (never message text), one row per
response, and decides every duplicate the same way whatever order files are read in.

Rules (SPEC R-94 .. R-101, R-104, R-186):
  * the kept copy of a key is `better()`: higher output, then a non-empty stop reason, then the
    later instant, then the remaining fields (a total order, so file order cannot matter);
  * a key found in several accounts' folders belongs to the first of them in the locked account
    order, which the caller passes as `folders` (E5.ordered: #1 first);
  * an unfinished reply is kept and flagged (complete = 0): its input and cache tokens are real;
  * a worktree agent's folder is credited to its repo; a dangling symlink counts as gone."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterator, List, NamedTuple, Optional, Sequence

from ju import timeutil
from ju.ledger_db import Log, Row, default_log, insert_record

_WORKTREE = re.compile(r"^(?P<repo>.*)/\.claude/worktrees/agent-[^/]+(?:/.*)?$")
_IN_CHUNK = 500


class ScanStats(NamedTuple):
    files: int         # transcript files read this run
    new: int           # keys that were not in the ledger before
    total: int         # rows in the ledger now
    gone: int          # listed files that no longer exist (dangling symlinks, deleted mid-scan)
    unreadable: int = 0


def project_of(cwd: Optional[str]) -> str:
    """Folder name of the session's working dir; a worktree agent's `<repo>/.claude/worktrees/agent-*`
    belongs to <repo>."""
    cwd = cwd or ""
    m = _WORKTREE.match(cwd)
    if m:
        cwd = m.group("repo")
    return Path(cwd).name or "?"


def _count(v) -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError(f"token count {v!r}")
    return v


def parse_file(path, folder_key: str, log: Optional[Log] = None) -> Iterator[Row]:
    """Every assistant response in one transcript, as Rows, in file order. Lines that are not JSON,
    not assistant replies, synthetic, or without a usable key or time are skipped; the last two
    are logged because they mean the transcript format changed."""
    log = log or default_log
    fallback = no_key = bad = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(o, dict) or o.get("type") != "assistant":
                continue
            m = o.get("message")
            u = m.get("usage") if isinstance(m, dict) else None
            if not isinstance(u, dict) or not o.get("timestamp"):
                continue
            model = m.get("model") or "unknown"
            if model.startswith("<"):                      # <synthetic> and friends
                continue
            mid, rid = m.get("id"), o.get("requestId")
            if mid and rid:
                key = f"{mid}:{rid}"
            elif o.get("uuid"):
                key, fallback = o["uuid"], fallback + 1
            else:
                no_key += 1
                continue
            try:
                local = timeutil.parse_iso(o["timestamp"]).astimezone()
                cc = u.get("cache_creation")
                cc = cc if isinstance(cc, dict) else {}
                c1h = _count(cc.get("ephemeral_1h_input_tokens"))
                c5m = cc.get("ephemeral_5m_input_tokens")
                c5m = max(_count(u.get("cache_creation_input_tokens")) - c1h, 0) if c5m is None else _count(c5m)
                vals = (_count(u.get("input_tokens")), c5m, c1h,
                        _count(u.get("cache_read_input_tokens")), _count(u.get("output_tokens")))
            except ValueError:                             # no offset on the time, or a token count that is not a count
                bad += 1
                continue
            stop = m.get("stop_reason") or None
            yield Row(key, timeutil.iso(local), local.strftime("%Y-%m-%d"), folder_key, model,
                      project_of(o.get("cwd")), o.get("sessionId") or "?", *vals, stop,
                      0 if stop is None else 1)
    if fallback:
        log("info", f"{path}: {fallback} response(s) keyed by uuid (no message id + request id)")
    if no_key:
        log("warning", f"{path}: {no_key} response(s) skipped, no message id, request id or uuid")
    if bad:
        log("warning", f"{path}: {bad} response(s) skipped, time without offset or unreadable token counts")


def _order(r: Row) -> tuple:
    """Total order behind better(): output, has a stop reason, instant, then every other field."""
    return (r.output, r.stop_reason is not None, timeutil.parse_iso(r.ts).timestamp(),
            r.input, r.cache_5m, r.cache_1h, r.cache_read, r.stop_reason or "",
            r.model, r.project, r.session, r.ts)


def better(a: Row, b: Row) -> Row:
    """The copy to keep: higher output, then non-empty stop reason, then later ts."""
    return a if _order(a) >= _order(b) else b


def _from_db(r: sqlite3.Row) -> Row:
    vals = [r[c] for c in Row._fields]
    row = Row(*vals)
    return row._replace(complete=0 if row.stop_reason is None else 1)    # the flag is derived, never trusted


def upsert(conn: sqlite3.Connection, rows: Sequence[Row], rank: Dict[str, int]) -> int:
    """Merge `rows` into the ledger: per key keep better()'s token data and the account that comes
    first in `rank` (account key -> position in the locked order; an account not in `rank` comes
    last). Returns how many keys were new. The caller owns the transaction."""
    unknown = len(rank)
    pos = lambda acct: rank.get(acct, unknown)
    best: Dict[str, Row] = {}
    for r in rows:
        cur = best.get(r.key)
        if cur is None:
            best[r.key] = r
        else:
            keep = better(cur, r)
            best[r.key] = keep._replace(account=min(cur.account, r.account, key=lambda a: (pos(a), a)))
    keys = list(best)
    existing: Dict[str, Row] = {}
    for i in range(0, len(keys), _IN_CHUNK):
        chunk = keys[i:i + _IN_CHUNK]
        q = f"SELECT * FROM records WHERE key IN ({','.join('?' * len(chunk))})"
        for rec in conn.execute(q, chunk):
            existing[rec["key"]] = _from_db(rec)
    new = 0
    for k, r in best.items():
        old = existing.get(k)
        if old is None:
            new += 1
        else:
            keep = better(old, r)
            r = keep._replace(account=min(old.account, r.account, key=lambda a: (pos(a), a)))
        insert_record(conn, r)
    return new


def _transcripts(folder: str) -> List[str]:
    """Every projects/**/*.jsonl under a config folder (subagents and workflows included), sorted.
    Dangling symlinks are listed: the caller counts them as gone."""
    root = os.path.join(folder, "projects")
    out, seen = [], set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        dirnames.sort()
        out.extend(os.path.join(dirpath, n) for n in sorted(filenames) if n.endswith(".jsonl"))
    return out


def scan(conn: sqlite3.Connection, folders: Sequence, full: bool = False,
         log: Optional[Log] = None) -> ScanStats:
    """Ingest every transcript that changed (size or mtime) since the last scan; `full` rereads all.
    `folders` = config folder keys in locked account order (#1 first). One transaction per file,
    so a long first scan never holds the write lock for the whole run."""
    log = log or default_log
    keys = [str(f) for f in folders]
    rank = {k: i for i, k in enumerate(keys)}
    seen = {} if full else {r[0]: (r[1], r[2]) for r in conn.execute("SELECT path, size, mtime FROM files")}
    read = new = gone = unreadable = 0
    for folder in keys:
        for path in _transcripts(folder):
            try:
                st = os.stat(path)
            except FileNotFoundError:
                gone += 1
                continue
            except OSError as e:
                unreadable += 1
                log("error", f"{path}: cannot stat ({e.strerror}); skipped")
                continue
            if seen.get(path) == (st.st_size, st.st_mtime):
                continue
            try:
                rows = list(parse_file(path, folder, log=log))
            except FileNotFoundError:
                gone += 1
                continue
            except OSError as e:
                unreadable += 1
                log("error", f"{path}: cannot read ({e.strerror}); skipped")
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                new += upsert(conn, rows, rank)
                conn.execute("INSERT OR REPLACE INTO files (path, size, mtime) VALUES (?,?,?)",
                             (path, st.st_size, st.st_mtime))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            read += 1
    total = conn.execute("SELECT count(*) FROM records").fetchone()[0]
    return ScanStats(read, new, total, gone, unreadable)
