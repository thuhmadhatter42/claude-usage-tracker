### 1.7 Feed schema 2 (engine → app; also the dashboard's per-account block shape)

```json
{"schema": 2, "version": "0.2.0", "generated": "ISO", "next_due": "ISO",
 "rules": {"stale_after_s": 900, "red_at": 97, "warmup_e": 0.03, "read_every_s": 60},
 "me": {"user": "Sam", "install_id": "a1b2"},
 "accounts": [ {
   "key": "/abs/.claude", "number": 1, "email": "x@y", "uuid": "…", "folders": ["/abs/.claude"],
   "weekly_reset": "ISO|null", "flags": ["using_credits", "reset_available"],
   "warning": "text|null", "error": {"kind": "rate_limited", "text": "…", "until": "ISO|null"}|null,
   "no_session_meter": false,
   "meters": [ {"id": "session|weekly|weekly:fable", "name": "Session|Weekly|Weekly · Fable",
     "pct": 37.0, "resets_at": "ISO", "read_at": "ISO", "source": "service|statusline|last_good",
     "period_s": 18000, "disagree": false,
     "pace": {"e": 0.41, "landing": 90.0|null, "state": "green|amber|red", "forecast": true,
              "runs_out": "ISO|null", "reset_due": false, "words": "On pace: about 90% by the reset"},
     "split": {"period_start": "ISO", "parts": [{"user": "Sam", "install_id": "a1b2", "usd": 412.0, "share": 0.71}]}|null } ],
   "session_share": {"current": 4.0|null, "last": 11.0|null, "median7": 9.0|null,
                     "list": [{"start": "ISO", "pct": 11.0}], "as_of": "ISO"}|null,
   "missing_people": ["Lee"] } ],
 "totals": {"as_of": "ISO", "today": {"tokens": 1, "usd": 1.0, "calls": 1, "unpriced": 0},
            "week": {"tokens": 1, "usd": 1.0, "unpriced": 0}},
 "windows": [{"account": "/abs/.claude", "start": "ISO", "end": "ISO", "tokens": 1, "usd": 1.0, "open": true}],
 "models": [{"id": "claude-opus-5", "name": "opus-5", "tokens": 1, "usd": 1.0}],
 "projects": [{"name": "x", "share": 0.4, "tokens": 1}],
 "months": [{"key": "2026-11", "label": "November", "tokens": 1, "usd": 1.0, "calls": 1,
             "types": {"input": 1, "cache_w": 1, "cache_read": 1, "output": 1}, "models": [], "projects": []}],
 "people": [{"install_id": "c3d4", "user": "Lee", "host": "mac", "generated": "ISO",
             "today": {}, "week": {}, "accounts": [<account block, meters without runs_out>]}],
 "statusline": {"/abs/.claude": {"enabled": false, "capture_at": "ISO|null"}},
 "dashboard": "/abs/dashboard.html|null",
 "share": {"written_at": "ISO|null", "error": "text|null"},
 "notes": ["skipped Lee@mac-c3d4.json: report_version 1"]}
```
Rules: `accounts` sorted by `number`. Every number on screen has an age source: meter `read_at`, `totals.as_of`, `session_share.as_of`, person `generated`. Golden copy: `tests/fixtures/feed-v2.json` (T1), decoded by S1 tests and rendered by D2 tests.
