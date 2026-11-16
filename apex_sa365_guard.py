"""APEX-FETCHSTATUS-2026-10-23 (owner, "Go" 2026-10-23): dead-key fast stop.

On 10/23 SportsAPI365 answered 401 to every call from 1:44 PM. The 3:03 PM /fetch
still tried ~8 name lookups per prop (about 5 s each prop) and ran 40 minutes before
posting a near-empty board. This module is the shared switch: after 3 refusals
(401/403) in a row, every SportsAPI365 call in this process is skipped (returns
"no data" at once) until the run resets it, 10 minutes pass, or the key file changes.
"""
import os
import threading
import time

KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sportapi365_key.txt")
REFUSALS_TO_TRIP = 3
DEAD_TTL_S = 600

_lock = threading.Lock()
_st = {"streak": 0, "dead_at": None, "code": None, "key_mtime": None, "skipped": 0}


def _key_mtime():
    try:
        return os.path.getmtime(KEY_FILE)
    except Exception:
        return None


def reset():
    """Start of a run (/fetch, auto-eval): forget earlier refusals."""
    with _lock:
        _st.update(streak=0, dead_at=None, code=None, key_mtime=_key_mtime(), skipped=0)


def is_dead():
    with _lock:
        if _st["dead_at"] is None:
            return False
        if time.time() - _st["dead_at"] > DEAD_TTL_S or _key_mtime() != _st["key_mtime"]:
            _st.update(streak=0, dead_at=None, code=None)
            return False
        _st["skipped"] += 1
        return True


def record(status):
    """Report one SportsAPI365 answer (HTTP status code). Returns True when this call tripped the switch."""
    with _lock:
        if status in (401, 403):
            _st["streak"] += 1
            if _st["dead_at"] is None and _st["streak"] >= REFUSALS_TO_TRIP:
                _st.update(dead_at=time.time(), code=status, key_mtime=_key_mtime())
                print("[SA365Guard] key refused %d times in a row (HTTP %s) -- skipping SportsAPI365 calls"
                      % (_st["streak"], status))
                return True
        elif status == 200:
            _st.update(streak=0, dead_at=None, code=None)
        return False


def status():
    with _lock:
        return dict(_st)


def preflight():
    """One live call before a run. Returns (ok, reason). A 401/403 marks the key dead right away."""
    try:
        import apex_price_feed as _apf
        payload, err = _apf._api("/extend/api/events/live")
    except Exception as e:
        return True, "check skipped (%s)" % type(e).__name__   # never block a run on the check itself
    if payload is not None:
        return True, "answering"
    code = str(err or "").replace("HTTP ", "").strip()
    if code in ("401", "403"):
        with _lock:
            _st.update(streak=REFUSALS_TO_TRIP, dead_at=time.time(), code=int(code), key_mtime=_key_mtime())
        return False, "HTTP " + code
    return True, str(err or "no answer")   # timeouts / 5xx: not a dead key, let the run try


REFUSED_TEXT = ("\U0001F6D1 **SportsAPI365 key refused** — boards can't be scored. "
                "Fix the key, then run /fetch again.")
