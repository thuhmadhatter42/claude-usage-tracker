"""E1: every number the engine shares, in one place. Owners of behaviour read these; nobody
re-types them. Tested value by value in tests/engine/test_constants.py."""
from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent                # .../engine/ju
REPO_ROOT = HERE.parent.parent                        # holds VERSION, in the repo and in the app bundle alike
STATE_DEFAULT = Path.home() / ".claude-usage"

# Staleness and colour (one pace function, one clock)
STALE_AFTER_S = 900
RED_AT = 97.0
WARMUP_E = 0.03
SPAN_FLOOR_DIV = 7
RUNS_OUT_LANDING = 105
SESSION_S = 18000
WEEK_S = 604800

# Read cadence and backoff
READ_EVERY_DEFAULT = 60
READ_EVERY_MIN = 15
READ_EVERY_MAX = 600
BACKOFF_S = (300, 600, 1200, 1800)
RETRY_AFTER_CAP_S = 3600
JITTER = 0.20
MANUAL_FLOOR_S = 15

# Timeouts
KEYCHAIN_TIMEOUT_S = 30
HTTP_TIMEOUT_S = 15
DB_BUSY_S = 10

SHARE_MIN_INTERVAL_S = 300

# The usage service
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_BETA = "oauth-2025-04-20"

# Identity checks
FINGERPRINT_TOL_S = 120
DISAGREE_PCT = 5
DISAGREE_RESET_S = 120

# Token columns, in ledger order
COLS = ("input", "cache_5m", "cache_1h", "cache_read", "output")

# Schema versions
FEED_SCHEMA = 2
LIVE_SCHEMA = 2
DB_SCHEMA = 3
REPORT_VERSION = 3


def state_dir(override: str | None) -> Path:
    """The state folder: `--state-dir` (tests, dev build) or ~/.claude-usage. An empty override
    is a caller bug, not a request for the default."""
    if override is None:
        return STATE_DEFAULT
    if override == "":
        raise ValueError("state dir override is empty")
    return Path(os.path.expanduser(override)).absolute()
