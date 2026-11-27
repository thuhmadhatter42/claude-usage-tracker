"""Engine tests. The engine package is `ju`, found the way production finds it: the `engine/`
folder is on sys.path (claude_usage.py runs from there). There is deliberately no `engine`
package, so no module can be imported twice under two names (two copies of an exception class
would stop `except` from matching)."""
import sys
from pathlib import Path

_ENGINE = str(Path(__file__).resolve().parents[2] / "engine")
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)
