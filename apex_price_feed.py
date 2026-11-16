#!/usr/bin/env python3
"""APEX-PRICE-FEED-SA365-2026-09-23 -- SportsAPI365 replacement for apex_price_feed.py.
Same public functions the core uses: get_price(), last_reason(), fixture_context().

fixture_context(a, b): which event this pair is in (tournament, tour, start time, started,
  best-of-3/5 hint) from SportsAPI365's upcoming + live lists.
get_price(a, b): de-vigged favourite probability. SportsAPI365 has NO Pinnacle price before
  a match starts, so pre-match pricing can only come from Bet365. The engine was fit on
  Pinnacle, so Bet365 stays OFF (BET365_PREMATCH_ENABLED = False) until the
  Bet365-vs-Pinnacle gap study clears it. While off, every call declines with that reason.
  The returned dict keeps the old key names (pinn_fav etc.) so the core needs no change;
  'book' always says which book really priced it.
Never raises; every failure returns None with a logged reason."""
import re, json, time, threading, unicodedata, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

MARKER = "APEX-PRICE-FEED-SA365-2026-09-23"
BASE = "https://api.sportsapi365.com/v1/tennis"
KEY_FILE = Path(__file__).resolve().parent / "sportapi365_key.txt"
UA = "Mozilla/5.0 (ApexPriceFeed)"   # Cloudflare blocks the default Python name tag (error 1010)
HTTP_TIMEOUT, HTTP_TRIES = 12, 2
LIST_TTL_SEC = 10 * 60           # upcoming lists
LIVE_TTL_SEC = 2 * 60            # live list
STALE_OK_SEC = 6 * 60 * 60       # service down -> keep using the last good list this long
MAX_UPCOMING_PAGES = 12
START_GRACE_MIN = 2
MAX_PRICE_AGE_MIN = 180
MAX_PRICE_AGE_MIN_EARLY = 720       # APEX-PRICEAGE-2026-10-11: unchanged price OK up to 12h when the match is 3h+ away
PRICE_AGE_STRICT_WINDOW_H = 3.0     # inside this many hours of the start, the 180-min limit applies
BET365_PREMATCH_ENABLED = True   # APEX-BET365-ON-2026-09-23: gap study cleared aces/dfs/breaks/total_sets/games_won/total_games; fantasy_score gated separately
BET365_LEAN_CORRECTION = 0.0053  # measured 09-23 on 368 matches: Bet365 favourite prob runs ~0.53pt low vs Pinnacle on average; added back before use

FORMAT_BO3_MAX_TOTAL = 27.0      # main total at or below this -> best of 3
FORMAT_BO5_MIN_TOTAL = 32.0      # main total at or above this -> best of 5
SLAM_NAMES = ("us open", "australian open", "roland garros", "french open", "wimbledon")
SLAM_KEYS = {"us open": "us_open", "australian open": "aus_open", "roland garros": "french_open",
             "french open": "french_open", "wimbledon": "wimbledon"}
SLAM_MAIN_DRAW_START = {         # YYYY-MM-DD of the main draw's first day (UTC date)
    "us open": {"2026": "2026-08-30"},   # APEX-FMT-001: Day 1 was Sun 08-30
}
DEC_MIN, DEC_MAX = 1.001, 1000.0
BOOK_SUM_MIN, BOOK_SUM_MAX = 1.00, 1.25
PROB_MIN, PROB_MAX = 0.01, 0.99
VERBOSE = True

_REASONS = []
_LOCK = threading.Lock()
_CACHE = {}   # name -> (fetched_ts, value)


def last_reason():
    """The most specific decline messages from the most recent get_price() call."""
    picks = [r for r in _REASONS if r.startswith(("DECLINE", "sa365"))]
    return "; ".join(picks[-2:]) if picks else ""


def log(msg):
    try:
        if isinstance(msg, str) and msg.startswith(("DECLINE", "sa365")):
            _REASONS.append(msg)
            del _REASONS[:-12]
    except Exception:
        pass
    if VERBOSE:
        print(f"    [price_feed] {msg}")


class Quota:
    """Kept so older callers that pass quota= still work. No per-call credits here."""
    def __init__(self, *a, **k):
        pass


def _key():
    try:
        return KEY_FILE.read_text().strip()
    except Exception:
        return ""


def _api(path):
    key = _key()
    if not key:
        return None, "no SportsAPI365 key file"
    import apex_sa365_guard as _g   # APEX-FETCHSTATUS-2026-10-23: dead-key fast stop
    if _g.is_dead():
        return None, "HTTP %s (key refused, skipped)" % (_g.status().get("code") or 401)
    err = None
    for i in range(HTTP_TRIES):
        req = urllib.request.Request(BASE + path, headers={
            "X-Gravitee-Api-Key": key, "Accept": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                _out = json.loads(r.read().decode())
                _g.record(200)
                return _out, None
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            _g.record(e.code)
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(1.5 * (i + 1))
    return None, err


# ----------------------------------------------------------------- names (kept from the old feed)
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace("-", " ").replace(".", " ").replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()


def _tokens(s):
    return [t for t in _norm(s).split() if t]


def _surname_initial(s):
    """'Osorio Serrano, Maria Camila' or 'Maria Camila Osorio Serrano' -> ('serrano','m')"""
    raw = str(s or "")
    if "," in raw:
        last, first = raw.split(",", 1)
    else:
        t = _tokens(raw)
        if not t:
            return ("", "")
        first, last = " ".join(t[:-1]), t[-1]
    lt, ft = _tokens(last), _tokens(first)
    return (lt[-1] if lt else "", ft[0][0] if ft else "")


def match_pair(want_a, want_b, home, away):
    """Match an unordered name pair against one event. Returns method or None."""
    wa, wb = _norm(want_a), _norm(want_b)
    ha, ha2 = _norm(home), _norm(away)
    if {wa, wb} == {ha, ha2}:
        return "exact"
    def tset(s):
        return frozenset(_tokens(s))
    if len(tset(wa)) >= 2 and len(tset(wb)) >= 2 and {tset(wa), tset(wb)} == {tset(ha), tset(ha2)}:
        return "exact"
    # APEX-NAMEMATCH-2026-11-02 (owner, "first and last name should be the go to ... sometimes it has
    # their middle names"): same first and last name, one side adding middle names or a second surname.
    def full(x, y):
        tx, ty = _tokens(x), _tokens(y)
        if len(tx) < 2 or len(ty) < 2 or tx[0] != ty[0]:
            return False
        if tx[-1] != ty[-1] and not (set(tx) <= set(ty) or set(ty) <= set(tx)):
            return False
        return set(tx) <= set(ty) or set(ty) <= set(tx)
    if (full(wa, ha) and full(wb, ha2)) or (full(wa, ha2) and full(wb, ha)):
        return "exact"
    ka, kb = _surname_initial(want_a), _surname_initial(want_b)
    kh, kh2 = _surname_initial(home), _surname_initial(away)
    def hit(k, j):
        if not k[0] or not j[0] or k[0] != j[0]:
            return False
        return (not k[1]) or (not j[1]) or k[1] == j[1]
    if (hit(ka, kh) and hit(kb, kh2)) or (hit(ka, kh2) and hit(kb, kh)):
        return "surname_initial"
    return None


def _is(a, b):
    """Single-name version of match_pair."""
    return bool(match_pair(a, "zz zz", b, "zz zz"))


def devig(dec_1, dec_2):
    """Two-way de-vig. Returns (p1, p2, book_sum) or (None, None, reason)."""
    try:
        d1, d2 = float(dec_1), float(dec_2)
    except (TypeError, ValueError):
        return None, None, "non-numeric price"
    for d in (d1, d2):
        if not (DEC_MIN <= d <= DEC_MAX):
            return None, None, f"decimal price out of range: {d}"
    i1, i2 = 1.0 / d1, 1.0 / d2
    s = i1 + i2
    if not (BOOK_SUM_MIN <= s <= BOOK_SUM_MAX):
        return None, None, f"implied total {s:.4f} outside {BOOK_SUM_MIN}-{BOOK_SUM_MAX}"
    p1, p2 = i1 / s, i2 / s
    if not (PROB_MIN <= p1 <= PROB_MAX):
        return None, None, f"de-vigged prob {p1:.4f} implausible"
    return p1, p2, s


# ----------------------------------------------------------------- event lists
def _rows(p):
    if isinstance(p, dict):
        for k in ("results", "result", "data"):
            if isinstance(p.get(k), list):
                return p[k]
    return p if isinstance(p, list) else []


def _start_secs(e):
    v = e.get("startTimestamp")
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e11 else v
    return None


def _names(e):
    def one(p, q):
        v = e.get(p)
        if v:
            return str(v)
        v = e.get(q)
        return str(v.get("name") or "") if isinstance(v, dict) else str(v or "")
    return one("participant1", "player1"), one("participant2", "player2")


def _tour_guess(e):
    lg = str(e.get("league") or "").lower()
    t = f"{e.get('tourType') or ''} {lg}".lower()
    return "wta" if ("wta" in t or "women" in t or re.match(r"\s*w\d", lg)) else "atp"


def _cached(name, ttl, fetch, allow_spend):
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(name)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if not allow_spend:
        return hit[1] if hit else None
    val = fetch()
    if val is not None:
        with _LOCK:
            _CACHE[name] = (now, val)
        return val
    if hit and now - hit[0] < STALE_OK_SEC:
        log(f"sa365 refresh of {name} failed -- using the copy from {int((now - hit[0]) / 60)} min ago")
        return hit[1]
    return None


def _book_save(rows):
    """APEX-TENNISSETTLE-2026-11-15: remember every SportsAPI365 match id we see (players + day), so the settler can find a finished
    match by its id when the name lookup misses (SportsAPI365 looks names up by full legal name: Taylor Harry Fritz, Cori Gauff). Never raises."""
    try:
        import json as _j, os as _o, time as _t
        f = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "data", "sa365_event_book.json")
        try:
            book = _j.load(open(f))
        except Exception:
            book = {}
        cut = _t.time() - 30 * 86400
        for e in rows or []:
            if e.get("id") and e.get("participant1") and e.get("participant2") and "/" not in str(e.get("participant1")):
                book[str(e["id"])] = dict(id=e["id"], participant1=e["participant1"], participant2=e["participant2"], startTimestamp=e.get("startTimestamp"),
                                          dateOnly=e.get("dateOnly"), league=e.get("league"))
        book = {k: v for k, v in book.items() if float(v.get("startTimestamp") or 0) >= cut}
        with open(f + ".tmp", "w") as fh:
            _j.dump(book, fh)
        _o.replace(f + ".tmp", f)
    except Exception:
        pass


def _fetch_upcoming(tour):
    out = []
    for page in range(1, MAX_UPCOMING_PAGES + 1):
        p, err = _api(f"/extend/api/events/upcoming/{tour}?page={page}")
        if err:
            log(f"sa365 upcoming/{tour} page {page}: {err}")
            return out if out else None
        rows = _rows(p)
        if not rows:
            break
        for e in rows:
            e["_tour"] = tour
            out.append(e)
        if len(rows) < 50:
            break
    _book_save(out)   # APEX-TENNISSETTLE-2026-11-15
    return out


def _fetch_live():
    p, err = _api("/extend/api/events/live")
    if err:
        log(f"sa365 live list: {err}")
        return None
    rows = _rows(p)
    for e in rows:
        e["_tour"] = _tour_guess(e)
    _book_save(rows)   # APEX-TENNISSETTLE-2026-11-15
    return rows


def _events(allow_spend=True):
    ev = []
    for tour in ("atp", "wta"):
        ev += _cached("upcoming_" + tour, LIST_TTL_SEC, lambda t=tour: _fetch_upcoming(t), allow_spend) or []
    ev += _cached("live", LIVE_TTL_SEC, _fetch_live, allow_spend) or []
    return ev


def _find(player_a, player_b, allow_spend=True):
    """-> (event, method, why)"""
    evs = _events(allow_spend)
    if not evs:
        return None, None, "no SportsAPI365 match lists available"
    cands, seen = [], set()
    for e in evs:
        h, a = _names(e)
        if "/" in h or "/" in a:
            continue
        if str(e.get("status") or "").lower().startswith("cancel"):
            continue
        m = match_pair(player_a, player_b, h, a)
        if m and e.get("id") not in seen:
            seen.add(e.get("id")); cands.append((e, m))
    if not cands:
        return None, None, f"no SportsAPI365 match for {player_a} vs {player_b}"
    cands.sort(key=lambda c: (0 if str(c[0].get("status") or "").lower() == "not started" else 1,
                              _start_secs(c[0]) or 9e12))
    if len(cands) > 1:
        log(f"{len(cands)} SportsAPI365 rows for {player_a} vs {player_b} -- using the not-started / soonest one")
    return cands[0][0], cands[0][1], ""


# ----------------------------------------------------------------- context + price
def _started(e):
    st = str(e.get("status") or "").lower()
    if st and st != "not started":
        return True
    s = _start_secs(e)
    return bool(s and s <= time.time() + START_GRACE_MIN * 60)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z") if ts else None


def _slam(league):
    low = str(league or "").lower().replace(".", "")
    return next((s for s in SLAM_NAMES if s in low), None)


def _fetch_pm(eid):
    p, err = _api(f"/extend/api/odds/pre-match/{eid}")
    if err:
        log(f"sa365 pre-match {eid}: {err}")
        return None
    return (p or {}).get("result") or {}


def _prematch(eid, allow_spend=True):
    return _cached(f"prematch_{eid}", 5 * 60, lambda: _fetch_pm(eid), allow_spend)


def _main_total(pm):
    """The Bet365 total-games line whose two prices are closest together."""
    best = None
    for r in (pm.get("Over Under") or []):
        try:
            ln, a, b = float(r["line"]), float(r["od1"]), float(r["od2"])
        except Exception:
            continue
        if best is None or abs(a - b) < best[0]:
            best = (abs(a - b), ln)
    return best[1] if best else None


def fixture_context(player_a, player_b, quota=None, allow_spend=True):
    """What SportsAPI365 lists for this pair right now. Never raises. None if not listed."""
    try:
        e, meth, why = _find(player_a, player_b, allow_spend)
        if e is None:
            log(f"fixture_context: {why}")
            return None
        league = str(e.get("league") or e.get("tournament") or "")
        tour = e.get("_tour") or "atp"
        s = _start_secs(e)
        out = {"tournament": league, "category": tour.upper(), "tour": tour, "start_time": _iso(s),
               "started": _started(e), "main_total": None, "format_hint": None, "format_basis": None,
               "source": "sportsapi365", "fixture_id": e.get("id"),
               "match_method": f"sa365:{meth}", "marker": MARKER}
        slam = _slam(league)
        if tour == "wta":
            out["format_hint"], out["format_basis"] = "bo3", "women's singles"
            return out
        if not slam:
            out["format_hint"], out["format_basis"] = "bo3", "men's non-slam event"
            return out
        if not out["started"]:
            pm = _prematch(e.get("id"), allow_spend)
            mt = _main_total(pm) if pm else None
            out["main_total"] = mt
            if mt is not None and mt <= FORMAT_BO3_MAX_TOTAL:
                out["format_hint"], out["format_basis"] = "bo3", f"Bet365 total-games line {mt}"
            elif mt is not None and mt >= FORMAT_BO5_MIN_TOTAL:
                out["format_hint"], out["format_basis"] = "bo5", f"Bet365 total-games line {mt}"
        if out["format_hint"] is None and s:
            d = _iso(s)[:10]
            md = SLAM_MAIN_DRAW_START.get(slam, {}).get(d[:4])
            if md:
                out["format_hint"] = "bo3" if d < md else "bo5"
                out["format_basis"] = f"calendar: {d} is {'before' if d < md else 'in'} the {slam} main draw (from {md})"
        log(f"fixture_context: {league} | atp slam -> {out['format_hint'] or 'format unknown'} "
            f"({out['format_basis'] or 'no line, no calendar entry'})")
        return out
    except Exception as ex:
        log(f"fixture_context: unexpected error -- {type(ex).__name__}: {ex}")
        return None


def get_price(player_a, player_b, sport_keys=None, quota=None, allow_spend=True, use_oddspapi=None):
    """De-vigged favourite probability for a match, or None. Never raises."""
    try:
        del _REASONS[:]
        e, meth, why = _find(player_a, player_b, allow_spend)
        if e is None:
            log(f"DECLINE: {why}")
            return None
        if _started(e):
            log(f"DECLINE: {player_a} vs {player_b} has already started -- no pre-match price")
            return None
        if not BET365_PREMATCH_ENABLED:
            log("DECLINE: SportsAPI365 has no pre-match Pinnacle price; Bet365 pricing stays off "
                "until the Bet365-vs-Pinnacle gap study clears it")
            return None
        pm = _prematch(e.get("id"), allow_spend)
        rows = [r for r in ((pm or {}).get("Full Time Result") or [])
                if str(r.get("bookmaker", "")).lower() == "bet365"]
        if not rows:
            log(f"DECLINE: no Bet365 match price for {player_a} vs {player_b}")
            return None
        r = max(rows, key=lambda x: float(x.get("addTime") or 0))
        age = (time.time() - float(r.get("addTime") or 0)) / 60
        # APEX-PRICEAGE-2026-10-11 (owner, approved "yes, keep" 2026-10-11):
        # addTime is when bet365 last CHANGED the price, not when it was read. A
        # day-ahead match whose odds simply haven't moved for 3+ hours was being
        # declined (Oct 11: Bencic v Zhang 17.5 MORE 73%, Kalinskaya v Parry 18.5
        # MORE 74.8% dropped off the boards). The 3-hour limit now applies only
        # within 3 hours of the start; earlier, an unchanged price is accepted for
        # up to 12 hours. The pre-start re-pricing job re-checks board props with
        # the strict limit inside the last 2 hours.
        _limit = MAX_PRICE_AGE_MIN
        try:
            _st = _start_secs(e)
            if _st and (_st - time.time()) / 3600.0 > PRICE_AGE_STRICT_WINDOW_H:
                _limit = MAX_PRICE_AGE_MIN_EARLY
        except Exception:
            pass
        if age > _limit:
            log(f"DECLINE: Bet365 price is {age:.0f} min old (limit {_limit})")
            return None
        p1, p2, s = devig(r.get("od1"), r.get("od2"))
        if p1 is None:
            log(f"DECLINE: Bet365 price unusable -- {s}")
            return None
        # APEX-BET365-ON-2026-09-23: correct the measured Bet365 lean before use
        if p1 >= p2:
            p1 = min(max(p1 + BET365_LEAN_CORRECTION, PROB_MIN), PROB_MAX); p2 = 1 - p1
        else:
            p2 = min(max(p2 + BET365_LEAN_CORRECTION, PROB_MIN), PROB_MAX); p1 = 1 - p2
        h, a = _names(e)
        fav, dog, pf = (h, a, p1) if p1 >= p2 else (a, h, p2)
        if _is(player_a, fav) and not _is(player_b, fav):
            fav, dog = player_a, player_b
        elif _is(player_b, fav) and not _is(player_a, fav):
            fav, dog = player_b, player_a
        league = str(e.get("league") or "")
        tour = e.get("_tour") or "atp"
        slug = SLAM_KEYS.get(_slam(league)) or re.sub(r"[^a-z0-9]+", "_", league.lower()).strip("_")
        return {"ok": True, "fav": fav, "dog": dog, "pinn_fav": round(pf, 4), "book": "bet365",
                "match_method": f"sa365:{meth}", "sport_title": league, "sport_key": f"tennis_{tour}_{slug}",
                "vig_pct": round((s - 1) * 100, 2), "credits_spent": 0, "quota_remaining": "n/a",
                "price_age_min": round(age, 1), "event_id": e.get("id"), "start_time": _iso(_start_secs(e)),
                "source": "sportsapi365", "marker": MARKER}
    except Exception as ex:
        log(f"DECLINE: unexpected error -- {type(ex).__name__}: {ex}")
        return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        a, b = sys.argv[1], sys.argv[2]
        print("fixture_context:", json.dumps(fixture_context(a, b), indent=1))
        print("get_price:", get_price(a, b))
        print("last_reason:", last_reason())
    else:
        evs = _events()
        print(f"{MARKER}: {len(evs)} matches listed (upcoming men's + women's, plus live)")
        for e in sorted(evs, key=lambda x: _start_secs(x) or 9e12)[:12]:
            print("  ", e.get("_tour"), "|", e.get("status"), "|", " vs ".join(_names(e)), "|", e.get("league"))
