#!/usr/bin/env python3
"""
OpenClaw V8 — Tennis Stats API Integration (ms-profile)
DEMETRIUS System — owner

Built around confirmed working endpoints:
  - profile/search/{name}/{tour}     → name-based player lookup
  - profile/{name}/surface-summary   → surface records per year
  - profile/{name}/match-stat/{year} → hold%, break%, aces, first serve%
  - profile/{name}/upcoming-match    → Gate 11 auto-feed
  - h2h/{player1}/{player2}          → H2H records

Drop into: /path/to/home/openclaw/
"""

import os
import re
import json
import time
import datetime
import difflib
import requests
from pathlib import Path
import apex_sa365_guard as _sa365_guard   # APEX-FETCHSTATUS-2026-10-23

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

# APEX-V8-SA365-2026-09-23: all player-stats calls go to SportsAPI365 (same data, same
# routes -- its /profile/... and /{tour}/... paths are the same /tennis/v2 backend).
from pathlib import Path as _SAPath
SA365_BASE = "https://api.sportsapi365.com/v1/tennis"
try:
    _SA365_KEY = (_SAPath(__file__).resolve().parent / "sportapi365_key.txt").read_text().strip()
except Exception:
    _SA365_KEY = ""
BASE_URL = SA365_BASE
HEADERS = {
    "X-Gravitee-Api-Key": _SA365_KEY,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (ApexStats)",   # Cloudflare blocks default Python name tags
}
HEADERS_V2 = HEADERS

# Years to aggregate stats from (last 3 years weighted)
STAT_YEARS = [2026, 2025, 2024]
SURFACE_YEARS = 5  # how many years of surface records to aggregate
# ─────────────────────────────────────────────
# HOME/AWAY CLASSIFICATION MAPS (v7.9.7)
# ─────────────────────────────────────────────
# Root cause of the all-zeros home_wins bug: the V8 API match object
# does NOT return countryAcr on either the tournament or player objects.
# Both lookups always returned None, condition was always False, and
# _record_home_away_result() never fired once. Every match got logged
# as away by the slip-result fallback path instead.
# Fix: static maps keyed by names we DO have from the API response.

TOURNAMENT_COUNTRY_MAP = {
    "wimbledon":          "GBR",
    "us open":            "USA",
    "australian open":    "AUS",
    "roland garros":      "FRA",
    "miami open":         "USA",
    "indian wells":       "USA",
    "madrid open":        "ESP",
    "rome":               "ITA",
    "canadian open":      "CAN",
    "toronto":            "CAN",
    "montreal":           "CAN",
    "cincinnati":         "USA",
    "shanghai":           "CHN",
    "paris":              "FRA",
    "vienna":             "AUT",
    "halle":              "DEU",
    "queens":             "GBR",
    "eastbourne":         "GBR",
    "nottingham":         "GBR",
    "birmingham":         "GBR",
    "berlin":             "DEU",
    "bad homburg":        "DEU",
    "hertogenbosch":      "NLD",
    "ilkley":             "GBR",
    "modena":             "ITA",
    "lyon":               "FRA",
    "geneva":             "CHE",
    "barcelona":          "ESP",
    "monte carlo":        "MCO",
    "dubai":              "ARE",
    "doha":               "QAT",
    "adelaide":           "AUS",
    "brisbane":           "AUS",
    "sydney":             "AUS",
    "auckland":           "NZL",
    "washington":         "USA",
    "atlanta":            "USA",
    "los cabos":          "MEX",
    "umag":               "HRV",
    "bastad":             "SWE",
    "gstaad":             "CHE",
    "hamburg":            "DEU",
    "newport":            "USA",
    "kitzbuhel":          "AUT",
    "winston-salem":      "USA",
    "cleveland":          "USA",
    "chengdu":            "CHN",
    "beijing":            "CHN",
    "tokyo":              "JPN",
    "osaka":              "JPN",
    "seoul":              "KOR",
    "wuhan":              "CHN",
    "guadalajara":        "MEX",
    "san diego":          "USA",
    "tallinn":            "EST",
    "monastir":           "TUN",
    "linz":               "AUT",
    "cluj-napoca":        "ROU",
}

PLAYER_NATIONALITY_MAP = {
    # ATP
    "novak djokovic":          "SRB",
    "carlos alcaraz":          "ESP",
    "jannik sinner":           "ITA",
    "daniil medvedev":         "RUS",
    "alexander zverev":        "DEU",
    "andrey rublev":           "RUS",
    "holger rune":             "DNK",
    "casper ruud":             "NOR",
    "taylor fritz":            "USA",
    "tommy paul":              "USA",
    "ben shelton":             "USA",
    "frances tiafoe":          "USA",
    "felix auger-aliassime":   "CAN",
    "denis shapovalov":        "CAN",
    "rafael nadal":            "ESP",
    "stefanos tsitsipas":      "GRC",
    "grigor dimitrov":         "BGR",
    "hubert hurkacz":          "POL",
    "karen khachanov":         "RUS",
    "sebastian korda":         "USA",
    "jiri lehecka":            "CZE",
    "tomas machac":            "CZE",
    "alex de minaur":          "AUS",
    "ugo humbert":             "FRA",
    "arthur fils":             "FRA",
    "gael monfils":            "FRA",
    "alexander bublik":        "KAZ",
    "jack draper":             "GBR",
    "cameron norrie":          "GBR",
    "andy murray":             "GBR",
    "dan evans":               "GBR",
    "matteo berrettini":       "ITA",
    "lorenzo musetti":         "ITA",
    "roberto bautista agut":   "ESP",
    "pablo carreno busta":     "ESP",
    "david goffin":            "BEL",
    "stan wawrinka":           "CHE",
    "nicolas jarry":           "CHL",
    "francisco cerundolo":     "ARG",
    "diego schwartzman":       "ARG",
    "sebastian baez":          "ARG",
    # WTA
    "iga swiatek":             "POL",
    "aryna sabalenka":         "BLR",
    "coco gauff":              "USA",
    "elena rybakina":          "KAZ",
    "jessica pegula":          "USA",
    "marketa vondrousova":     "CZE",
    "ons jabeur":              "TUN",
    "karolina muchova":        "CZE",
    "barbora krejcikova":      "CZE",
    "petra kvitova":           "CZE",
    "karolina pliskova":       "CZE",
    "naomi osaka":             "JPN",
    "madison keys":            "USA",
    "sloane stephens":         "USA",
    "sofia kenin":             "USA",
    "danielle collins":        "USA",
    "emma navarro":            "USA",
    "amanda anisimova":        "USA",
    "victoria azarenka":       "BLR",
    "elina svitolina":         "UKR",
    "marta kostyuk":           "UKR",
    "daria kasatkina":         "RUS",
    "liudmila samsonova":      "RUS",
    "mirra andreeva":          "RUS",
    "diana shnaider":          "RUS",
    "maria sakkari":           "GRC",
    "beatriz haddad maia":     "BRA",
    "jasmine paolini":         "ITA",
    "elena-gabriela ruse":     "ROU",
    "simona halep":            "ROU",
    "caroline garcia":         "FRA",
    "leylah fernandez":        "CAN",
    "bianca andreescu":        "CAN",
    "emma raducanu":           "GBR",
    "katie boulter":           "GBR",
    "harriet dart":            "GBR",
    "ajla tomljanovic":        "AUS",
    "storm hunter":            "AUS",
    "zhang shuai":             "CHN",
    "qinwen zheng":            "CHN",
    "belinda bencic":          "CHE",
    "donna vekic":             "HRV",
    "petra martic":            "HRV",
    "magda linette":           "POL",
    "veronika kudermetova":    "RUS",
    "elsa jacquemot":          "FRA",
}


# ─────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent.resolve()
# v7.9.6: consolidated from a separate top-level database/ folder into
# data/cache/, alongside everything else openclaw stores. Run
# migrate_data_layout.py once before this version runs for the first
# time — it safely moves the old database/ folder into this location.
DB_DIR     = BASE_DIR / "data" / "cache"
DB_DIR.mkdir(parents=True, exist_ok=True)
CACHE_PATH = DB_DIR / "v8_api_cache.json"

# v7.9.7 (fuzzy-match safety net, this session): a real list of known
# player names to check a failed resolution against. Seeded once from
# an operator-provided list of current top players (both tours), and
# grows automatically every time resolve_player_name() succeeds on a
# NEW name it hasn't seen before — combining both approaches discussed:
# a real starting list (immediate coverage) plus organic growth (covers
# players not in the seed list over time, with zero extra API calls).
# Root cause this addresses: a vision-model misread (e.g. "Pegula" ->
# "Peguia") produces a name that's genuinely close to a real player but
# not an exact match — V8's own search has no fuzzy tolerance, so a
# one-letter OCR error meant total resolution failure even though the
# real player is well known to this system.
KNOWN_PLAYERS_FILE = BASE_DIR / "data" / "known_players.json"
KNOWN_PLAYERS_FILE.parent.mkdir(parents=True, exist_ok=True)
_FUZZY_MATCH_MAX_DISTANCE = 2  # max edit distance to count as a likely match

# v7.9.6: per-player home/away win rate, accumulated live from real
# matches this system processes over time — see
# DEMETRIUS_CALIBRATION_NOTES.md for why this exists and the research
# behind it. Lives in learning/ alongside openclaw_core.py's other
# accumulated-over-time files (signal_history.json etc.), not in
# cache/ — this isn't a re-fetchable API cache, it's real accumulated
# data that should never be casually wiped.
HOME_AWAY_DIR  = BASE_DIR / "data" / "learning"
HOME_AWAY_DIR.mkdir(parents=True, exist_ok=True)
HOME_AWAY_FILE = HOME_AWAY_DIR / "home_away_splits.json"

CACHE_TTL = {
    "search":          720,   # 30 days
    "surface_summary": 168,   # 7 days
    "match_stat":      168,   # 7 days
    "h2h":             24,    # 24 hours
    "upcoming":        1,     # 1 hour
    "profile":         168,   # 7 days
}

def _load_cache() -> dict:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_cache(cache: dict):
    DB_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)

def _cache_get(key: str, ttl_hours: int):
    cache = _load_cache()
    if key not in cache:
        return None
    entry     = cache[key]
    cached_at = datetime.datetime.fromisoformat(entry["cached_at"])
    age_hours = (datetime.datetime.now() - cached_at).total_seconds() / 3600
    if age_hours > ttl_hours:
        return None
    return entry["data"]

def _cache_set(key: str, data):
    cache = _load_cache()
    cache[key] = {
        "cached_at": datetime.datetime.now().isoformat(),
        "data":      data,
    }
    _save_cache(cache)

# ─────────────────────────────────────────────
# HOME/AWAY SPLIT TRACKING (v7.9.6)
# ─────────────────────────────────────────────
# See DEMETRIUS_CALIBRATION_NOTES.md for why this exists. Accumulates
# forever, never auto-cleared — this is real accumulated data, not a
# re-fetchable cache.

def _load_home_away_splits() -> dict:
    if HOME_AWAY_FILE.exists():
        with open(HOME_AWAY_FILE) as f:
            return json.load(f)
    return {}

def _save_home_away_splits(splits: dict):
    with open(HOME_AWAY_FILE, "w") as f:
        json.dump(splits, f, indent=2, default=str)

def _record_home_away_result(canonical_name: str, is_home: bool, won: bool):
    """
    Records one verified match result toward a player's accumulated
    home/away split. Only ever called with a VERIFIED win/loss — never
    tally an unconfirmed result, per the same discipline used
    everywhere else in this system.
    """
    splits = _load_home_away_splits()
    key = canonical_name.lower()
    entry = splits.get(key, {"home_wins": 0, "home_losses": 0,
                              "away_wins": 0, "away_losses": 0})
    if is_home:
        entry["home_wins" if won else "home_losses"] += 1
    else:
        entry["away_wins" if won else "away_losses"] += 1
    entry["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d")
    splits[key] = entry
    _save_home_away_splits(splits)

def get_home_away_split(canonical_name: str, min_sample: int = 5) -> dict:
    """
    Returns a player's accumulated home/away win rate, or a status
    indicating the sample is too thin to use yet. min_sample applies
    to BOTH home and away counts independently — matching the
    MIN_PROPOSAL_SAMPLE discipline used for weight proposals
    elsewhere. Never returns a usable rate below that threshold; a
    thin split is real data but not yet trustworthy, same as every
    other "unconfirmed is not assumed neutral" rule in this system.
    """
    splits = _load_home_away_splits()
    entry = splits.get(canonical_name.lower())
    if not entry:
        return {"status": "NO_DATA", "home_win_pct": None, "away_win_pct": None}

    home_total = entry["home_wins"] + entry["home_losses"]
    away_total = entry["away_wins"] + entry["away_losses"]

    if home_total < min_sample or away_total < min_sample:
        return {
            "status": "THIN_SAMPLE",
            "home_sample": home_total, "away_sample": away_total,
            "home_win_pct": None, "away_win_pct": None,
        }

    return {
        "status": "CONFIRMED",
        "home_sample": home_total, "away_sample": away_total,
        "home_win_pct": round(entry["home_wins"] / home_total * 100, 1),
        "away_win_pct": round(entry["away_wins"] / away_total * 100, 1),
    }

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def log(tag: str, msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [V8] [{tag}] {msg}")

# ─────────────────────────────────────────────
# RAW API
# ─────────────────────────────────────────────

def _do_request(url: str, params: dict = None, headers: dict = None):
    # v7.9.6: headers is now optional so _api_get_v2 can pass HEADERS_V2
    # (correct x-rapidapi-host for the second host) instead of always
    # using the module-level HEADERS built for the original host.
    # Existing callers that don't pass headers keep the exact old
    # behavior — nothing about _api_get() or its callers changes.
    request_headers = headers if headers is not None else HEADERS
    _sa365 = url.startswith(SA365_BASE)
    if _sa365 and request_headers is HEADERS:   # APEX-FETCHSTATUS-2026-10-23: a new key file is used without a restart
        try:
            _k = (_SAPath(__file__).resolve().parent / "sportapi365_key.txt").read_text().strip()
            if _k and _k != HEADERS.get("X-Gravitee-Api-Key"):
                HEADERS["X-Gravitee-Api-Key"] = _k
        except Exception:
            pass
    if _sa365 and _sa365_guard.is_dead():   # APEX-FETCHSTATUS-2026-10-23: key refused -- skip, no 5 s retry chain
        return None
    try:
        resp = requests.get(url, headers=request_headers, params=params, timeout=15)
        if resp.status_code == 429:
            log("RateLimit", "Rate limited — waiting 3s...")
            time.sleep(3)
            resp = requests.get(url, headers=request_headers, params=params, timeout=15)
        if _sa365:
            _sa365_guard.record(resp.status_code)
        if resp.status_code != 200:
            log("Error", f"HTTP {resp.status_code} on {url}")
            return None
        return resp.json()
    except Exception as e:
        log("Error", f"Request failed on {url}: {e}")
        return None

def _api_get(endpoint: str, params: dict = None):
    """Calls the 'ms-api' surface (BASE_URL). This is what the rest of
    this file has always used — player search, surface-summary,
    match-stat, matches-played."""
    return _do_request(f"{BASE_URL}/{endpoint}", params)

def _api_get_v2(tour: str, endpoint: str, params: dict = None):
    """
    v7.9.6 Section 2B fix. Calls the SEPARATE host
    tennis-api-atp-wta-itf.p.rapidapi.com — confirmed by the vendor's
    support team and by a direct RapidAPI console test (200 OK,
    2026-07-03) as a distinct subscribed product from the ms-api
    surface this file otherwise uses. Needed specifically for
    h2h/stats, match-stats, and perf-breakdown, which only exist on
    this surface — calling them against RAPIDAPI_HOST was always a 404
    because that endpoint path was never deployed on that host at all.
    """
    url = f"{SA365_BASE}/{tour}/{endpoint}"   # APEX-V8-SA365-2026-09-23
    return _do_request(url, params, headers=HEADERS_V2)

# ─────────────────────────────────────────────
# PLAYER NAME RESOLUTION
# ─────────────────────────────────────────────

def _load_known_players() -> dict:
    """Loads the known-players reference list: {"atp": [...], "wta": [...]}."""
    if not KNOWN_PLAYERS_FILE.exists():
        return {"atp": [], "wta": []}
    try:
        with open(KNOWN_PLAYERS_FILE) as f:
            data = json.load(f)
        return {"atp": data.get("atp", []), "wta": data.get("wta", [])}
    except Exception:
        return {"atp": [], "wta": []}


def _add_known_player(canonical_name: str, tour: str) -> None:
    """
    Grows the known-players list every time resolve_player_name()
    succeeds on a name not already in it. This is the "auto-growth"
    half of the fuzzy-match safety net — the seed list gives immediate
    coverage, this keeps it current with zero extra API calls, since
    it just records names the system already confirmed via a real
    successful resolution.
    """
    tour = tour if tour in ("atp", "wta") else "atp"
    data = _load_known_players()
    if canonical_name not in data[tour]:
        data[tour].append(canonical_name)
        try:
            with open(KNOWN_PLAYERS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log("Search", f"Could not update known_players.json (non-fatal): {e}")


def _fuzzy_match_known_player(name: str, tour: str):
    """
    Compares `name` against the known-players list for this tour and
    returns the closest match if it's within _FUZZY_MATCH_MAX_DISTANCE
    edits (character insertions/substitutions/deletions) — tight enough
    to catch a genuine one- or two-letter OCR misread (e.g. "Peguia" vs
    "Pegula", 2 edits) without loosely matching two different real
    players' names to each other. Returns None if no candidate is close
    enough. NEVER silently substitutes without this being logged and
    flagged to the caller — a fuzzy match is a plausible guess, not a
    confirmed fact, and must always be presented as such downstream.
    """
    known = _load_known_players().get(tour, [])
    if not known:
        return None

    name_lower = name.lower().strip()
    best_match = None
    best_distance = _FUZZY_MATCH_MAX_DISTANCE + 1

    for candidate in known:
        candidate_lower = candidate.lower()
        # Quick length-based pre-filter avoids running full distance
        # calculation against names that couldn't possibly be close.
        if abs(len(candidate_lower) - len(name_lower)) > _FUZZY_MATCH_MAX_DISTANCE:
            continue
        # SequenceMatcher ratio approximates edit distance cheaply;
        # convert to an approximate edit-distance-like scale for the
        # threshold check rather than importing a separate library.
        ratio = difflib.SequenceMatcher(None, name_lower, candidate_lower).ratio()
        approx_distance = round((1 - ratio) * max(len(name_lower), len(candidate_lower)))
        if approx_distance <= _FUZZY_MATCH_MAX_DISTANCE and approx_distance < best_distance:
            best_distance = approx_distance
            best_match = candidate

    return best_match


import unicodedata as _unicodedata

def _strip_accents(s: str) -> str:
    """NFKD-decompose and drop combining marks -- 'Carreño' -> 'Carreno'.
    Reuses the exact approach already proven in tennis_warehouse_collector.py's
    _name_variants() for this same class of name; duplicated here (not
    imported) to keep this module's own dependency surface unchanged."""
    if not s:
        return s
    return "".join(c for c in _unicodedata.normalize("NFKD", s)
                    if not _unicodedata.combining(c))


def _short_form_variant(name: str) -> str:
    """For long compound/legal names (3+ words), try just the first two
    tokens -- e.g. 'Mayar Sherif Ahmed Abdelaziz' -> 'Mayar Sherif'. Real
    players are routinely indexed by their commonly-used short form
    rather than a full legal name. Only fires on 3+ word names -- a
    normal two-word name is left untouched, since there's nothing to
    shorten."""
    parts = name.split()
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1]}"
    return name


_KNOWN_NAMES_PATH = Path(__file__).parent / "data" / "known_player_names.json"
_known_names_cache = None

def _load_known_names():
    """
    2026-08-04: real, local, pre-seeded name reference built from the
    TennisMyLife match database (real official ATP/WTA names, MIT
    licensed, free to use) via build_name_reference.py. Loaded once and
    cached in memory -- this is a plain local file read, not an API
    call, so checking it first costs nothing real and never counts
    against any rate limit.

    Missing file is a normal, expected state (the operator may not have
    run the builder script yet, or it simply doesn't exist on this
    machine) -- degrades to an empty reference, never raises.
    """
    global _known_names_cache
    if _known_names_cache is not None:
        return _known_names_cache
    try:
        _known_names_cache = json.loads(_KNOWN_NAMES_PATH.read_text())
    except Exception:
        _known_names_cache = {"atp": {}, "wta": {}}
    return _known_names_cache


def resolve_player_name(name: str, tour: str = "atp") -> str:
    """
    Searches the API for the canonical player name.
    Returns the exact name string the API uses, or None if not found.
    Cached 30 days.

    v7.9.7 fix (this session): normalizes "Last, First" comma-format
    names to "First Last" before any search or fallback logic runs.
    Confirmed via direct testing: "Coco Gauff" (space-format) correctly
    resolved via the last-name fallback ("Gauff" extracted correctly by
    split()[-1]), but "Gauff, Coco" (comma-format, as it actually comes
    through from real screenshot/OCR intake) failed completely —
    split()[-1] on the comma-format string extracts "Coco" (her FIRST
    name) as the fallback "last name" to search, which V8 doesn't
    recognize as a surname at all. Same root problem affected multiple
    real evaluations tonight (Gauff, Mertens, others formatted
    "Surname, Firstname"). Normalizing once at the top fixes the API
    query, the last-name fallback, AND the final matching loop all at
    once, rather than patching each of the three separately.
    """
    # APEX-HOTFIX-2026-09-28 FIX #3: strip intake artifacts ("@ ", "vs ",
    # "vs. ", stray leading/trailing whitespace) that the parser sometimes
    # forwards attached to an otherwise-valid player name (confirmed root
    # cause of 124 of 178 real Gate 11 rejections this audit: "@ Kamil
    # Majchrzak", "@ Jaume Munar", "vs Linda Noskova" are all valid,
    # resolvable players once the artifact prefix is removed). Applied
    # BEFORE the comma-format normalization below so a contaminated name
    # in either format ("@ Gauff, Coco") is still handled correctly.
    # Case-insensitive, strips only a LEADING artifact (never touches text
    # mid-name), and re-checks up to twice in case of a doubled artifact
    # (e.g. "@ vs Player Name" from stacked intake bugs).
    if name:
        original_intake_name = name
        stripped = name.strip()
        for _ in range(2):
            prev = stripped
            if stripped.lower().startswith("@"):
                stripped = stripped[1:].strip()
            if stripped.lower().startswith("vs."):
                stripped = stripped[3:].strip()
            elif stripped.lower().startswith("vs "):
                stripped = stripped[3:].strip()
            elif stripped.lower() == "vs":
                stripped = ""
            if stripped == prev:
                break
        # APEX-GATE11-OPPSUFFIX-2026-09-28 defensive backstop: a
        # confirmed live bug had PrizePicks' "1st Set Total Games"/
        # "1st Set Total Games Won" tabs scraped with a trailing market-
        # scope suffix baked into the opponent name (e.g. "Alex Molcan
        # 1st Set"), which caused 158/158 real Gate 11 rejections in a
        # single run before this fix -- every one resolved cleanly once
        # the suffix was stripped. Fixed at the scrape source
        # (fetch_props_playwright in openclaw_core.py), but stripped
        # here too as a second line of defense in case any other future
        # caller forwards this same kind of trailing artifact -- same
        # reasoning as the leading "@"/"vs" strip just above, just for
        # a suffix instead of a prefix.
        _TRAILING_SET_SUFFIX_RE = re.compile(
            r"\s+(?:1st|2nd|3rd|first|second|third)\s+set\s*$", re.IGNORECASE)
        _desuffixed = _TRAILING_SET_SUFFIX_RE.sub("", stripped).strip()
        if _desuffixed != stripped:
            stripped = _desuffixed
        stripped = " ".join(stripped.split())  # collapse internal extra spacing
        if stripped and stripped != original_intake_name:
            name = stripped
            log("Search", f"Intake artifact stripped from name: '{original_intake_name}' -> '{name}'")

    # Detect and normalize "Last, First" -> "First Last". A single comma
    # with exactly two parts is the reliable signal — anything else
    # (no comma, multiple commas, suffixes) is left untouched rather
    # than risk mis-normalizing a name this pattern wasn't meant for.
    original_name = name
    if name and "," in name:
        parts = [p.strip() for p in name.split(",")]
        if len(parts) == 2 and parts[0] and parts[1]:
            name = f"{parts[1]} {parts[0]}"
            log("Search", f"Comma-format name normalized: '{original_name}' -> '{name}'")

    cache_key = f"search_{tour}_{name.lower().strip()}"
    cached = _cache_get(cache_key, CACHE_TTL["search"])
    if cached:
        return cached

    # 2026-08-04: check the real, local, pre-seeded name reference
    # first -- a plain in-memory dict lookup, free and instant, before
    # ever touching the cache or the live API. Confirmed real fix for a
    # live rejection this session (Aliona Falei vs Carol Young Suh Lee
    # -- Falei herself resolved fine via the API, but her opponent's
    # unusual multi-part name did not; both are real, current WTA
    # players and would be present in the TennisMyLife reference from
    # their real recent matches). Exact match only, case-insensitive --
    # deliberately NOT fuzzy here, since this table is built from real,
    # confirmed official names and an exact hit is a genuine, certain
    # resolution, not a guess.
    known = _load_known_names().get(tour, {})
    exact_hit = known.get(name.lower().strip())
    if exact_hit:
        _cache_set(cache_key, exact_hit)
        return exact_hit

    # Try full name first
    encoded = name.strip().replace(" ", "%20")
    data = _api_get(f"profile/search/{encoded}/{tour}")

    # v7.9.7 fix (this session, SECOND pass -- see extended debugging
    # session): match_against tracks which spelling the eventual
    # matching comparison (name_lower, below) should use. The FIRST
    # version of this fix only updated match_against inside the
    # hyphen-fallback branch, which only runs when earlier attempts
    # FAILED. But live debugging confirmed the actual, more common
    # failure mode: the full-name search above (Step 1) ITSELF
    # SUCCEEDS even with the hyphen still in the query (V8's search
    # endpoint tolerates it) — but the vendor's RETURNED result is
    # always spelled WITHOUT the hyphen. So Step 1 would return real
    # data, `if not data:` would be False, the hyphen-fallback branch
    # would never run, and match_against would stay hyphenated forever
    # — causing the exact-match and last-name-match checks below to
    # fail against a perfectly good result, exactly like it did for
    # Djokovic vs Auger-Aliassime tonight. Fix: whenever the input name
    # contains a hyphen at all, immediately set match_against to the
    # dehyphenated form — regardless of which step actually finds the
    # data — since the vendor never spells results with a hyphen once
    # this pattern is in play.
    match_against = name.replace("-", " ") if "-" in name else name

    if not data:
        # Try last name only
        last = name.strip().split()[-1]
        data = _api_get(f"profile/search/{last}/{tour}")

    if not data:
        # 2026-08-04 real fix: multi-word compound surname fallback.
        # The last-name-only attempt above only ever tries the single
        # final word -- for a 3+ word name like "Carol Young Suh Lee"
        # (a Western first name plus a compound Korean-style surname),
        # that means trying just "Lee" alone. If the vendor actually
        # stores her under the fuller compound surname ("Young Suh
        # Lee"), a single-word search can come back empty or genuinely
        # ambiguous (a common surname on its own matching nobody or the
        # wrong person). Confirmed as a real, live gap: Aliona Falei
        # vs Carol Young Suh Lee rejected at Gate 11 even though Falei
        # herself resolved fine -- only the opponent's compound name
        # was the actual problem. Only attempted for names with 3+
        # words, tried longest-to-shortest (last 3 words, then last 2)
        # so a genuine 2-word surname still gets its best real shot
        # before falling through to the plain single last word already
        # tried above.
        parts = name.strip().split()
        if len(parts) >= 3:
            for n_words in (3, 2):
                if len(parts) > n_words:
                    candidate = " ".join(parts[-n_words:])
                    if candidate == last:
                        continue  # already tried as the single-word case
                    log("Search", f"Compound-surname fallback: '{name}' -> trying '{candidate}'")
                    cand_encoded = candidate.strip().replace(" ", "%20")
                    data = _api_get(f"profile/search/{cand_encoded}/{tour}")
                    if data:
                        break

    if not data:
        # v7.9.7 fix (this session): hyphen-to-space fallback. Confirmed
        # via direct testing that V8's vendor database stores compound/
        # hyphenated surnames WITHOUT the hyphen -- "Felix Auger-Aliassime"
        # (as commonly written/screenshotted) returns nothing from V8 on
        # either the full-name or last-name-only search, while "Felix
        # Auger Aliassime" (hyphen replaced with a space) resolves
        # correctly. Confirmed as the root cause of an entire 8-prop
        # batch being rejected at Gate 11 (Djokovic vs Auger-Aliassime),
        # since Gate 11 no longer has a web-search fallback to catch
        # this the way it might have before tonight's V8-only rewrite.
        # Only attempted if the name actually contains a hyphen, and
        # only as a further fallback after the original-format attempts
        # already failed, so this can't interfere with names that
        # genuinely do use a hyphen in V8's own records (if any exist).
        if "-" in name:
            dehyphenated = name.replace("-", " ")
            log("Search", f"Hyphen fallback: '{name}' -> '{dehyphenated}'")
            dehyph_encoded = dehyphenated.strip().replace(" ", "%20")
            data = _api_get(f"profile/search/{dehyph_encoded}/{tour}")
            if not data:
                last = dehyphenated.strip().split()[-1]
                data = _api_get(f"profile/search/{last}/{tour}")

    if not data or not isinstance(data, list):
        # v7.9.7 (fuzzy-match safety net, this session): before giving
        # up entirely, check whether `name` is a close (<=2 character
        # edits) match to a real player already known to this system.
        # This is a GUESS, not a confirmed API result — it's logged
        # loudly and the caller must be able to tell it apart from a
        # real resolution (see resolution_failed/fuzzy_matched handling
        # in _build_stats). Directly addresses the Pegula/Peguia case:
        # a vision-model misread produced a name V8 has zero record of
        # under any spelling, but which is one clear letter-swap away
        # from a real, well-known player.
        fuzzy = _fuzzy_match_known_player(name, tour)
        if fuzzy:
            log("Search", f"⚠️ FUZZY MATCH (no API result) — '{name}' -> "
                f"'{fuzzy}' — this is a GUESS, not a confirmed API match. "
                f"Verify manually if this affects a real pick.")
            # Re-attempt a real API search using the fuzzy-matched name,
            # so the return value is still a genuine, API-confirmed
            # canonical name rather than just echoing our own guess back.
            retry_encoded = fuzzy.strip().replace(" ", "%20")
            retry_data = _api_get(f"profile/search/{retry_encoded}/{tour}")
            if retry_data and isinstance(retry_data, list):
                for result in retry_data:
                    if result.lower() == fuzzy.lower():
                        _cache_set(cache_key, result)
                        return result
                # API had SOME result but not an exact match to our
                # guess — still return the fuzzy guess itself rather
                # than nothing, since it's the best information we have,
                # but do NOT cache it (an unconfirmed guess shouldn't
                # persist as if it were a solid resolution).
                return fuzzy
            return fuzzy
        log("Search", f"No results for {name} ({tour})")
        # Fix (2026-07-23, approved -- root-caused live via Gate 11
        # rejecting Pablo Carreno Busta and Maiar/Mayar Sherif on real,
        # current ATP/WTA matches): every earlier attempt above worked
        # from the name exactly as given. Two real, recurring failure
        # shapes were never covered: an accented surname spelled without
        # its accent (or vice versa) -- the same root cause already fixed
        # for tennis_warehouse_collector.py's _name_variants(), but never
        # wired into THIS function, which Gate 11 calls directly -- and a
        # long compound/legal name where the player is actually indexed
        # under a shorter, more commonly-used form. Tried in this order,
        # original spelling always having been tried first via every
        # attempt above -- these are additive last resorts. `_api_get`
        # returns a plain list of name STRINGS (confirmed against the
        # exact-match block below, not a list of dicts) -- matched the
        # same way here: exact case-insensitive string compare only, no
        # substring/fuzzy guessing, since a variant retry finding SOME
        # result already carries real risk of a wrong-player match.
        for variant, why in (
            (_strip_accents(name), "accent-stripped"),
            (_short_form_variant(name), "short-form"),
            (_strip_accents(_short_form_variant(name)), "accent-stripped + short-form"),
        ):
            if variant == name:
                continue
            v_name_lower = variant.lower().strip()
            log("Search", f"Variant retry ({why}): '{name}' -> '{variant}'")
            v_encoded = variant.strip().replace(" ", "%20")
            v_data = _api_get(f"profile/search/{v_encoded}/{tour}")
            if v_data and isinstance(v_data, list):
                for result in v_data:
                    if result.lower() == v_name_lower:
                        _cache_set(cache_key, result)
                        log("Search", f"Resolved via {why} variant: '{name}' -> '{result}'")
                        _add_known_player(result, tour)
                        return result

        log("Search", f"Could not resolve (including variant retries): {original_name}")
        return None

    name_lower = match_against.lower().strip()

    # Exact match
    for result in data:
        if result.lower() == name_lower:
            _cache_set(cache_key, result)
            log("Search", f"Resolved: {name} → {result}")
            _add_known_player(result, tour)
            return result

    # Last name match
    # Bug fix (this session, confirmed live on a real pick): this
    # previously matched on LAST NAME ALONE, with zero check on first
    # name -- confirmed live that "Ariana Arseneault" (the real player
    # in the match being evaluated) resolved to "Nicolas Arseneault," a
    # completely different, unrelated player, purely because they share
    # a surname. Every stat in that evaluation (hold%, break%, aces,
    # recent matches) was silently for the wrong person -- worse than a
    # MISSING result, because nothing flagged it; it looked like a
    # normal, confirmed resolution. Unlike every other data-quality
    # issue found this session (MISSING, truncated, thin data), this
    # produced confidently WRONG data with no signal at all. Tennis has
    # many shared surnames, so this was very likely not a one-off.
    # Fix: a last-name-only match is no longer auto-accepted. First name
    # must also agree -- either exactly, or within the same tight
    # edit-distance threshold (_FUZZY_MATCH_MAX_DISTANCE) already used
    # for the fuzzy-match safety net elsewhere in this file, so a real
    # near-miss (a minor spelling variant) can still resolve while a
    # same-surname stranger cannot.
    last = name_lower.split()[-1]
    input_first = name_lower.split()[0] if len(name_lower.split()) > 1 else ""
    last_name_candidates = [r for r in data if r.lower().split()[-1] == last]
    for result in last_name_candidates:
        result_tokens = result.lower().split()
        result_first = result_tokens[0] if len(result_tokens) > 1 else ""
        if not input_first or not result_first:
            continue
        if input_first == result_first:
            _cache_set(cache_key, result)
            log("Search", f"Last-name match (first name exact): {name} → {result}")
            _add_known_player(result, tour)
            return result
        ratio = difflib.SequenceMatcher(None, input_first, result_first).ratio()
        approx_distance = round((1 - ratio) * max(len(input_first), len(result_first)))
        if approx_distance <= _FUZZY_MATCH_MAX_DISTANCE:
            _cache_set(cache_key, result)
            log("Search", f"Last-name match (first name close, {approx_distance} "
                f"edits): {name} → {result}")
            _add_known_player(result, tour)
            return result
    if last_name_candidates:
        rejected = ", ".join(last_name_candidates)
        log("Search", f"⚠️ Same-surname candidate(s) REJECTED for first-name "
            f"mismatch — '{name}' vs [{rejected}]. Not auto-accepting a "
            f"different person who happens to share a surname.")

    # v7.9.7 (fuzzy-match safety net): API returned SOME data, but
    # nothing matched exactly or by last name — try fuzzy matching
    # against the known-players list as a final attempt before giving up.
    fuzzy = _fuzzy_match_known_player(name, tour)
    if fuzzy:
        for result in data:
            if result.lower() == fuzzy.lower():
                log("Search", f"⚠️ FUZZY MATCH (from API result set) — "
                    f"'{name}' -> '{result}' — GUESS, verify manually.")
                _cache_set(cache_key, result)
                return result

    log("Search", f"Could not resolve: {name}")
    return None

# ─────────────────────────────────────────────
# SURFACE SUMMARY
# ─────────────────────────────────────────────

def _fallback_surface_count_from_matches_played(canonical_name: str, surface: str) -> dict:
    """
    Bug fix (this session, confirmed live via direct raw API test AND
    independent real-world verification): surface-summary can be
    genuinely stale for a real, active player -- confirmed live for
    Francesca Curmi (WTA #268), whose surface-summary response
    contained exactly ONE entry (year=2023, all zeros) while she had a
    real, confirmed 2026-07-06 win on file via matches-played on the
    same day this was tested (independently verified via Tennis Majors:
    "Francesca Curmi defeated Oceane Dodin 6-4, 6-3", Contrexeville,
    clay). 2024, 2025, and 2026 entries were not present in the
    surface-summary response at all -- not zero-filled, genuinely
    absent. If SURFACE_YEARS filtering were discarding them, they'd
    still be present in the raw response; they are not. This is a
    vendor-side gap on that specific endpoint, not an OpenClaw
    filtering bug.

    SCOPE, DELIBERATELY LIMITED: this counts real matches played on the
    target surface (from tournament.court.name, an unambiguous raw
    field) to correct surface_total / data_tier when the primary
    endpoint has nothing -- it does NOT attempt to reconstruct wins and
    losses. Reason: this file's own debug_raw_match docstring already
    flags that "result" field semantics (does it list player1's score
    first, or the winner's score first, in each set?) are UNCONFIRMED.
    Both real examples checked this session (Curmi d. Dodin, Sinner d.
    Struff) happened to have player1 as the winner -- consistent with
    EITHER convention, since they produce identical output whenever
    player1 wins. Guessing which convention holds risks silently
    flipping every win into a loss project-wide. A match COUNT on a
    surface is unambiguous regardless of this open question; a W-L
    breakdown is not. Left for a future session once a match where
    player1 lost can be used to disambiguate the convention properly
    (see debug_raw_match's own docstring for the same open question).

    Same limitation as the match-stat fallback: matches-played appears
    capped at the ~10 most recent matches per player (confirmed via
    direct comparison against a top-ranked player, who also returned
    exactly 10) -- this recovers CURRENT surface activity a stale
    aggregate is missing, not a full career surface record.
    """
    encoded = canonical_name.replace(" ", "%20")
    data = _api_get(f"profile/{encoded}/matches-played")
    if not data or "singles" not in data or not data["singles"]:
        return None

    matches_found = 0
    for m in data["singles"]:
        court_name = ((m.get("tournament", {}) or {}).get("court", {}) or {}).get("name", "")
        if not court_name or court_name.lower() != surface.lower():
            continue
        p1 = m.get("player1", {}) or {}
        p2 = m.get("player2", {}) or {}
        if (p1.get("name", "").lower() == canonical_name.lower()
                or p2.get("name", "").lower() == canonical_name.lower()):
            matches_found += 1

    if matches_found == 0:
        return None

    return {
        "record":    "unconfirmed (see _note)",
        "wins":      None,
        "losses":    None,
        "total":     matches_found,
        "win_pct":   None,
        "_source":   "matches_played_fallback",
        "_note":     f"surface-summary returned no recent data; found "
                      f"{matches_found} real {surface} match(es) via "
                      f"matches-played instead. Win/loss NOT computed -- "
                      f"'result' field's player1-vs-winner-first "
                      f"convention is unconfirmed (see docstring). Match "
                      f"count is safe (from tournament.court.name); W-L "
                      f"record is not, and is deliberately left "
                      f"unconfirmed rather than guessed.",
    }

def get_surface_summary(canonical_name: str, surface: str) -> dict:
    """
    Returns aggregated surface record for the last SURFACE_YEARS years.
    Surface: 'grass' | 'clay' | 'hard' | 'ihard'
    """
    cache_key = f"surface_{canonical_name.lower()}_{surface}"
    cached = _cache_get(cache_key, CACHE_TTL["surface_summary"])
    if cached:
        return cached

    encoded = canonical_name.replace(" ", "%20")
    data = _api_get(f"profile/{encoded}/surface-summary")

    if not data or not isinstance(data, list):
        # Bug fix (this session): don't accept "vendor returned nothing"
        # as final -- try the matches-played fallback before giving up,
        # same principle as get_match_stats' fallback above.
        fallback = _fallback_surface_count_from_matches_played(canonical_name, surface)
        if fallback:
            log("V8", f"surface-summary returned no data at all for "
                f"{canonical_name} -- using matches-played fallback "
                f"({fallback['total']} {surface} match(es) found).")
            _cache_set(cache_key, fallback)
            return fallback
        result = {"record": "N/A", "wins": 0, "losses": 0, "total": 0, "win_pct": None}
        _cache_set(cache_key, result)
        return result

    # Aggregate last N years
    current_year = datetime.date.today().year
    wins   = 0
    losses = 0

    for entry in data:
        year = entry.get("year", 0)
        if year < current_year - SURFACE_YEARS:
            continue
        surf_data = entry.get(surface, {})
        wins   += surf_data.get("w", 0)
        losses += surf_data.get("l", 0)

    total = wins + losses

    if total == 0:
        # Bug fix (this session, confirmed live): surface-summary can
        # return entries that are missing the CURRENT year(s) entirely
        # (not zero-filled -- genuinely absent), while the player has
        # real, current matches on that surface via matches-played.
        # Confirmed for Francesca Curmi: surface-summary had only a
        # 2023 entry despite a real, independently-verified 2026-07-06
        # match. Try the fallback before accepting zero as final.
        fallback = _fallback_surface_count_from_matches_played(canonical_name, surface)
        if fallback:
            log("V8", f"surface-summary returned 0 {surface} matches for "
                f"{canonical_name} (data present but stale/incomplete for "
                f"recent years) -- matches-played found "
                f"{fallback['total']} real recent {surface} match(es) "
                f"instead; using fallback.")
            _cache_set(cache_key, fallback)
            return fallback

    result = {
        "record":  f"{wins}-{losses}",
        "wins":    wins,
        "losses":  losses,
        "total":   total,
        "win_pct": round(wins / total * 100, 1) if total > 0 else None,
        "_source": "surface_summary",
    }

    _cache_set(cache_key, result)
    return result

def get_current_season_surface_winpct(canonical_name: str, surface: str) -> float:
    """
    v7.9.6 (Moneyline Phase 2, S1). Current-CALENDAR-YEAR-only surface
    win rate — deliberately NOT the same as get_surface_summary(),
    which aggregates SURFACE_YEARS (multi-year). S1 is explicitly
    scoped to current-season form only, per the locked framework.

    Returns None if there's no current-season data on this surface —
    unconfirmed, never assumed from multi-year history.
    """
    cache_key = f"season_surface_{canonical_name.lower()}_{surface}"
    cached = _cache_get(cache_key, CACHE_TTL["surface_summary"])
    if cached:
        return cached

    encoded = canonical_name.replace(" ", "%20")
    data = _api_get(f"profile/{encoded}/surface-summary")

    if not data or not isinstance(data, list):
        _cache_set(cache_key, None)
        return None

    current_year = datetime.date.today().year
    wins, losses = 0, 0
    for entry in data:
        if entry.get("year", 0) != current_year:
            continue
        surf_data = entry.get(surface, {})
        wins   += surf_data.get("w", 0)
        losses += surf_data.get("l", 0)

    total = wins + losses
    result = round(wins / total * 100, 1) if total > 0 else None
    _cache_set(cache_key, result)
    return result

# ─────────────────────────────────────────────
# MATCH STATS — HOLD%, BREAK%, ACES
# ─────────────────────────────────────────────

def _fallback_stats_from_matches_played(canonical_name: str) -> dict:
    """
    Bug fix (this session, confirmed live on a real pick): the season-
    aggregate endpoint (match-stat/{year}) can genuinely return
    "games": 0 for a player who has real, recent matches on file --
    confirmed live for Francesca Curmi (WTA #268), who had a real
    2026-07-06 match (vs Oceane Dodin, 6-4 6-3, full per-point stats)
    sitting in matches-played while match-stat/2026 came back all-zero
    across every field. Direct raw API test confirmed this is a real
    external data lag on the aggregate endpoint, not a parsing bug --
    the raw match data genuinely exists one endpoint over.

    This computes a DELIBERATELY LIMITED fallback directly from raw
    per-match data. avg_aces and first_serve% map unambiguously to raw
    per-match fields (aces, firstServe/firstServeOf on the player's own
    stats block) -- no interpretation required. hold_pct/break_pct/
    bp_save_pct are deliberately NOT computed here: those require
    game-level serve/break accounting, and debug_raw_match's own
    docstring already flags that several fields in this raw match JSON
    (e.g. "draw") have UNCONFIRMED semantics -- this API was never
    fully documented. Guessing at game-level hold/break math from
    ambiguous point-level fields (e.g. is "breakPointsConverted" on a
    player's own stats block their return-side conversions, or
    something else?) risks fabricating a number that LOOKS real but
    isn't verified -- worse than an honest None, which at least visibly
    signals "not confirmed" rather than silently passing off a guess as
    data. Partial, confirmed data is better than complete, invented
    data. (avg_double_faults is intentionally untouched by this fix --
    it comes from a separate, surface-filtered function elsewhere
    (get_recent_matches), which correctly returns empty for a player
    with zero matches on the surface being evaluated. That's an honest
    "no surface history" result, not the same aggregate-endpoint-lag
    bug this fallback addresses, and is out of scope here.)
    """
    encoded = canonical_name.replace(" ", "%20")
    data = _api_get(f"profile/{encoded}/matches-played")
    if not data or "singles" not in data or not data["singles"]:
        return None

    total_aces      = 0
    total_1st_in    = 0
    total_1st_total = 0
    matches_found   = 0
    most_recent_date = None

    for m in data["singles"]:
        p1 = m.get("player1", {}) or {}
        p2 = m.get("player2", {}) or {}
        target = None
        if p1.get("name", "").lower() == canonical_name.lower():
            target = p1
        elif p2.get("name", "").lower() == canonical_name.lower():
            target = p2
        if not target:
            continue

        stats = target.get("stats", {}) or {}
        aces  = stats.get("aces")
        fs_in = stats.get("firstServe")
        fs_of = stats.get("firstServeOf")

        if aces is not None:
            total_aces += aces
        if fs_in is not None and fs_of:
            total_1st_in    += fs_in
            total_1st_total += fs_of

        matches_found += 1
        m_date = m.get("date")
        if m_date and (most_recent_date is None or m_date > most_recent_date):
            most_recent_date = m_date

    if matches_found == 0:
        return None

    return {
        "hold_pct":    None,  # not computed -- see docstring above
        "break_pct":   None,  # not computed -- see docstring above
        "avg_aces":    round(total_aces / matches_found, 1),
        "first_serve": round(total_1st_in / total_1st_total * 100, 1) if total_1st_total > 0 else None,
        "bp_save_pct": None,  # not computed -- see docstring above
        "matches":     matches_found,
        "_source":     "matches_played_fallback",
        "_note":       "Season-aggregate endpoint returned 0 games; this is "
                        "computed directly from raw match data instead. "
                        "hold_pct/break_pct/bp_save_pct intentionally left "
                        "unconfirmed (None) rather than guessed.",
        "_most_recent_match_date": most_recent_date,
    }

def get_match_stats(canonical_name: str, years: list = None) -> dict:
    """
    Returns aggregated serve/return stats across specified years.
    Calculates:
      hold_pct    — service games held / service games played
      break_pct   — return games won / return games played
      avg_aces    — total aces / matches played
      first_serve — first serve in %
      bp_save_pct — break points saved %
    """
    if years is None:
        years = STAT_YEARS

    cache_key = f"matchstat_{canonical_name.lower()}_{'-'.join(str(y) for y in years)}"
    cached = _cache_get(cache_key, CACHE_TTL["match_stat"])
    if cached:
        return cached

    encoded = canonical_name.replace(" ", "%20")

    # Aggregate across years
    total_games       = 0
    total_holds       = 0
    total_sv_games    = 0
    total_bp_faced    = 0
    total_bp_saved    = 0
    total_ret_games   = 0
    total_breaks      = 0
    total_aces        = 0
    total_matches     = 0
    total_1st_in      = 0
    total_1st_total   = 0

    for year in years:
        data = _api_get(f"profile/{encoded}/match-stat/{year}")
        if not data or data.get("games", 0) == 0:
            continue

        matches = data.get("games", 0)
        total_matches += matches

        # Service hold
        bp_serve = data.get("breakPointsServe", {})
        hold_val   = bp_serve.get("serviceHold", {})
        sv_gms_val = bp_serve.get("breakPointFacedGm", {})

        holds   = hold_val.get("value", 0)
        sv_gms  = hold_val.get("count", 0)
        total_holds   += holds
        total_sv_games += sv_gms

        # Break points
        bp_faced = bp_serve.get("breakPointFacedGm", {}).get("value", 0)
        bp_saved = bp_serve.get("breakPointSavedGm", {}).get("value", 0)
        total_bp_faced += bp_faced
        total_bp_saved += bp_saved

        # Return / break rate
        bp_rtn = data.get("breakPointsRtn", {})
        bp_won     = bp_rtn.get("breakPointWonGm", {}).get("value", 0)
        opp_hold   = bp_rtn.get("opponentHold", {})
        ret_gms    = opp_hold.get("count", 0)
        breaks     = ret_gms - opp_hold.get("value", 0)
        total_ret_games += ret_gms
        total_breaks    += breaks

        # Aces
        ace_data = data.get("serviceStats", {}).get("acesGm", {})
        total_aces += ace_data.get("value", 0)

        # First serve
        fs_data = data.get("serviceStats", {}).get("firstServe", {})
        total_1st_in    += fs_data.get("value", 0)
        total_1st_total += fs_data.get("count", 0)

    if total_matches == 0:
        # Bug fix (this session): try the raw matches-played fallback
        # before giving up entirely -- confirmed live that a real,
        # ranked, recently-active player (Curmi) can have the season
        # aggregate return zero while raw match data genuinely exists.
        fallback = _fallback_stats_from_matches_played(canonical_name)
        if fallback:
            log("V8", f"Aggregate stats endpoint returned 0 games for "
                f"{canonical_name} but matches-played has "
                f"{fallback['matches']} real match(es) on file -- using "
                f"partial fallback (aces/first-serve% only; hold%/break%/"
                f"bp_save% remain unconfirmed by design, not guessed).")
            _cache_set(cache_key, fallback)
            return fallback
        result = {
            "hold_pct":    None,
            "break_pct":   None,
            "avg_aces":    None,
            "first_serve": None,
            "bp_save_pct": None,
            "matches":     0,
        }
        _cache_set(cache_key, result)
        return result

    result = {
        "hold_pct":    round(total_holds / total_sv_games * 100, 1) if total_sv_games > 0 else None,
        "break_pct":   round(total_breaks / total_ret_games * 100, 1) if total_ret_games > 0 else None,
        "avg_aces":    round(total_aces / total_matches, 1) if total_matches > 0 else None,
        "first_serve": round(total_1st_in / total_1st_total * 100, 1) if total_1st_total > 0 else None,
        "bp_save_pct": round(total_bp_saved / total_bp_faced * 100, 1) if total_bp_faced > 0 else None,
        "matches":     total_matches,
    }

    _cache_set(cache_key, result)
    return result

# ─────────────────────────────────────────────
# H2H
# ─────────────────────────────────────────────

def get_h2h(canonical_p1: str, canonical_p2: str, surface: str = None, tour: str = "atp") -> dict:
    """
    Returns H2H record between two players.
    Optional surface filter.

    v7.9.6 fix (round 2): the previous fix correctly stopped fabricating
    a confirmed "0-0" on failure, but the endpoint being called
    (h2h/{name}/{name} on the ms-api surface) turned out to be the
    wrong endpoint entirely — confirmed directly by the vendor's
    support team, not a guess. Their documented H2H endpoint lives on
    a DIFFERENT API surface, requires numeric player IDs (not names),
    and takes surface as a real query parameter instead of filtering
    a "matches" array client-side after the fact:

        GET /tennis/v2/{atp|wta}/h2h/stats/{id1}/{id2}?surface=X

    Still returns status: "UNAVAILABLE" (not a fabricated 0-0) if IDs
    can't be resolved or the request fails — same discipline as before,
    just now hitting the endpoint that actually exists.

    ── Moneyline v1.4 retirement weighting — SPECIFIED, NOT ENABLED ──
    The locked v1.4 spec called for weighting H2H results by
    retirement status (Completed=100% / Trailing retirement=50% /
    Tied retirement=25% / Early retirement=excluded). Investigated
    this session and confirmed: this vendor endpoint returns only
    pre-aggregated win counts (matchesWon per player) with NO
    match-level detail and NO retirement flag anywhere in the
    response — the rule is correct and the architecture is correct,
    but this data source cannot support it. This is a genuine vendor
    data-availability gap, not a coding problem.

    Deliberately NOT worked around here — this function stays
    unchanged, proven, and retirement-blind but complete/fast/career-
    spanning. If retirement-aware weighting is wanted later, the
    right path is a NEW, separate function (e.g. get_h2h_weighted())
    built from get_recent_matches()'s per-match data (which DOES
    carry a "retired" flag) filtered to matches against a specific
    opponent — accepting a real tradeoff of recency-window limits in
    exchange for retirement awareness, rather than silently changing
    what "H2H" means everywhere this function is already relied on.
    """
    key1 = canonical_p1.lower()
    key2 = canonical_p2.lower()
    cache_key = f"h2h_{min(key1,key2)}_{max(key1,key2)}_{surface or 'all'}"
    cached = _cache_get(cache_key, CACHE_TTL["h2h"])
    if cached:
        return cached

    id1 = resolve_player_id(canonical_p1, tour)
    id2 = resolve_player_id(canonical_p2, tour)

    def _unavailable():
        result = {
            "status":          "UNAVAILABLE",
            "p1_wins": None, "p2_wins": None,
            "p1_surface_wins": None, "p2_surface_wins": None,
            "total": None
        }
        _cache_set(cache_key, result)
        return result

    if not id1 or not id2:
        log("H2H", f"Could not resolve player IDs for {canonical_p1} / {canonical_p2} — H2H unavailable.")
        return _unavailable()

    params = {"surface": surface.lower()} if surface else None
    raw = _api_get_v2(tour, f"h2h/stats/{id1}/{id2}", params)

    if not raw:
        return _unavailable()

    # v7.9.6 Section 2B parsing fix: confirmed against a real live
    # response on 2026-07-03 (Djokovic/Nadal, 60 real matches) — the
    # response wraps everything one level deeper under "data", and win
    # counts live inside per-player "player1Stats"/"player2Stats"
    # blocks as "matchesWon" — not the top-level "player1Wins" /
    # "matchesCount" shape this code originally assumed before any
    # real response had ever been seen (the endpoint had only ever
    # 404'd against the wrong host until today's host fix).
    data = raw.get("data", raw)

    matches_count_raw = data.get("matchesCount")
    matches_count = int(matches_count_raw) if matches_count_raw is not None else None

    p1_stats = data.get("player1Stats", {}) or {}
    p2_stats = data.get("player2Stats", {}) or {}
    p1_wins = p1_stats.get("matchesWon", 0) or 0
    p2_wins = p2_stats.get("matchesWon", 0) or 0

    result = {
        "status":          "CONFIRMED",
        "p1_wins":         p1_wins,
        "p2_wins":         p2_wins,
        "p1_surface_wins": p1_wins if surface else None,
        "p2_surface_wins": p2_wins if surface else None,
        "total":           matches_count if matches_count is not None else (p1_wins + p2_wins),
    }
    _cache_set(cache_key, result)
    return result

# ─────────────────────────────────────────────
# UPCOMING MATCH — Gate 11 feed
# ─────────────────────────────────────────────

def get_recent_matches(canonical_name: str, surface: str = None, limit: int = 5) -> list:
    """
    Returns recent match results for a player from matches-played endpoint.
    Optionally filters by surface.
    Returns list of dicts with: date, opponent, score, surface, tournament,
    winner (bool — did this player win), set_count, extended (bool)

    This is the stable replacement for web search scoreline retrieval.
    Feeds REQ1 (extender detection) in the proven-competitive MORE path.
    """
    cache_key = f"matches_{canonical_name.lower()}_{surface or 'all'}_{limit}"
    cached = _cache_get(cache_key, 6)  # 6 hour cache — recent matches update frequently
    if cached:
        return cached

    encoded = canonical_name.replace(" ", "%20")
    data = _api_get(f"profile/{encoded}/matches-played")

    if not data or "singles" not in data:
        log("Matches", f"No match data for {canonical_name}")
        return []

    surface_map = {
        "grass": "Grass",
        "clay": "Clay",
        "hard": "Hard",
        "ihard": "Hard",
    }
    surface_filter = surface_map.get(surface.lower(), None) if surface else None

    results = []
    uncertain_count = 0
    captured_own_id = None
    captured_own_rank = None
    for match in data["singles"]:
        try:
            # Determine if this player was player1 or player2
            p1 = match.get("player1", {})
            p2 = match.get("player2", {})
            p1_name = p1.get("name", "")
            p2_name = p2.get("name", "")

            name_lower = canonical_name.lower()
            if name_lower in p1_name.lower():
                player_role = "player1"
                opponent_name = p2_name
                captured_own_id = captured_own_id or p1.get("id")
                captured_own_rank = captured_own_rank or p1.get("currentRank")
            elif name_lower in p2_name.lower():
                player_role = "player2"
                opponent_name = p1_name
                captured_own_id = captured_own_id or p2.get("id")
                captured_own_rank = captured_own_rank or p2.get("currentRank")
            else:
                continue

            # Surface filter
            court_name = match.get("tournament", {}).get("court", {}).get("name", "")
            if surface_filter and court_name != surface_filter:
                continue

            # Parse score
            score = match.get("result", "")
            if not score:
                continue

            # ── WINNER DETERMINATION (v7.9.6 — verified against real results) ──
            # The old code guessed the winner from a "draw" field using two
            # hardcoded, unverified code lists ([2,5,6,9] / [3,4,7,8]). That
            # produced confident-but-wrong W/L flags (Draper/Fritz R1
            # conflict, both live test runs).
            #
            # Confirmed against two real, independently-verified Wimbledon
            # 2026 results (web search + ESPN/WTA):
            #   1. Gauff (player1) beat Sierra (player2) 6-3 3-6 7-6(7),
            #      draw=16 — NOT in either old code list, proving "draw"
            #      is unrelated to who won.
            #   2. Krejcikova (player1, unseeded underdog) beat Andreeva
            #      (player2, #5 seed) 4-6 7-5 6-4, draw=8 — the old code's
            #      list would have called this one for Andreeva. She lost.
            # Both confirm: the score string lists player1's games first
            # and player2's games second in every set, consistently — NOT
            # winner-first. Counting which side wins the majority of sets
            # from that fixed positional order correctly identified the
            # real winner both times, including a genuine upset (so it's
            # not just tracking the favorite). "draw" carries no win/loss
            # signal and is dropped from winner detection entirely.
            #
            # Two confirmed matches is solid evidence, not exhaustive proof.
            # If a future match ever produces a result that contradicts a
            # confirmed real outcome, run debug_raw_match() again and flag it.

            def _majority_set_winner(score_str):
                """Returns True if player1 (first-listed side in each set)
                won a majority of sets, False if player2 did, None if the
                score can't be parsed (retirement/walkover/bad data)."""
                p1_sets, p2_sets = 0, 0
                for s in score_str.split():
                    core = s.split("(")[0]
                    if "-" not in core:
                        continue
                    parts = core.split("-")
                    if len(parts) != 2:
                        continue
                    try:
                        a, b = int(parts[0]), int(parts[1])
                    except ValueError:
                        continue
                    if a > b:
                        p1_sets += 1
                    elif b > a:
                        p2_sets += 1
                if p1_sets == p2_sets:  # no sets parsed, or an impossible tie
                    return None
                return p1_sets > p2_sets  # True = player1 won the match

            player1_won = _majority_set_winner(score)

            if player1_won is None:
                is_winner = None
                winner_verified = False
                uncertain_count += 1
            else:
                is_winner = player1_won if player_role == "player1" else (not player1_won)
                winner_verified = True

            # ── HOME/AWAY SPLIT TRACKING (v7.9.7 fix) ──────────────
            # v7.9.7: countryAcr does not exist in the V8 API response.
            # Fix: resolve from TOURNAMENT_COUNTRY_MAP and
            # PLAYER_NATIONALITY_MAP using names we DO have.
            tournament_name_raw = match.get("tournament", {}).get("name", "").lower()
            tournament_country = None
            for keyword, country in TOURNAMENT_COUNTRY_MAP.items():
                if keyword in tournament_name_raw:
                    tournament_country = country
                    break
            player_country = PLAYER_NATIONALITY_MAP.get(canonical_name.lower())
            if winner_verified and player_country and tournament_country:
                is_home = (player_country == tournament_country)
                _record_home_away_result(canonical_name, is_home, is_winner)

            # Count sets
            sets = [s for s in score.split() if "-" in s.split("(")[0]]
            set_count = len(sets)
            extended = set_count >= 3 or any("7-6" in s or "7-5" in s for s in sets)

            # Check for retirement
            retired = "ret" in score.lower()

            tournament = match.get("tournament", {})

            # ── TOTAL GAMES (v7.9.6 — for 5E result verification) ──
            # Sum every set's games from both sides. Used to verify
            # total_games props against the confirmed final scoreline.
            # Also split per-side for the "Games Won" prop type.
            #
            # v7.9.7 fix: a retired/walkover match's final set is
            # incomplete (e.g. "6-4 2-1 ret") — summing its partial
            # games into total_games silently understates or distorts
            # any historical average built from recent_matches (this
            # was previously only guarded at the single-pick grading
            # step in verify_result_via_v8(), not here at the source
            # where historical averages are built). A retired match's
            # game count is not a real completed data point, so it is
            # nulled out the same way an unparseable score already is.
            total_games = 0
            player_games, opp_games = 0, 0
            games_ok = True
            for s in sets:
                core = s.split("(")[0]
                parts = core.split("-")
                if len(parts) != 2:
                    games_ok = False
                    continue
                try:
                    a, b = int(parts[0]), int(parts[1])
                    total_games += a + b
                    # a/b are player1/player2 games (positional, per the
                    # confirmed score-order finding above)
                    if player_role == "player1":
                        player_games += a; opp_games += b
                    else:
                        player_games += b; opp_games += a
                except ValueError:
                    games_ok = False
            if not games_ok or total_games == 0 or retired:
                total_games, player_games, opp_games = None, None, None

            # ── PER-MATCH BOX SCORE (v7.9.6 — for 5E result verification) ──
            # The raw API returns a "stats" block per player (aces, double
            # faults, break points converted, etc.) for THIS specific
            # match. The old code discarded it entirely. Captured here
            # under this player's own role, plus the opponent's, so
            # learning_agent() can verify non-games props (aces, DF, BP)
            # without a separate web search.
            this_player_raw = p1 if player_role == "player1" else p2
            opp_player_raw  = p2 if player_role == "player1" else p1
            player_match_stats   = this_player_raw.get("stats", {}) or {}
            opponent_match_stats = opp_player_raw.get("stats", {}) or {}

            # v7.9.8: h2h and per-player historical odds were both present
            # in the raw match object (confirmed via a real raw sample
            # pulled 2026-07-27: match["h2h"], player1["odd"],
            # player2["odd"]) but neither was ever extracted into this
            # function's returned dict, so neither reached the collector.
            # historical_odd here is the market price AS OF that already-
            # completed match -- not a live line -- pulled straight
            # through with no interpretation, same treatment as every
            # other raw field in this function. Storage-only; does not
            # touch live evaluation reasoning (Rule 9 governs reasoning
            # inputs, not warehouse capture).
            this_player_odd = this_player_raw.get("odd")
            opp_player_odd  = opp_player_raw.get("odd")

            results.append({
                "date":          match.get("date", "")[:10],
                "opponent":      opponent_name,
                "score":         score,
                "surface":       court_name,
                "tournament":    tournament.get("name", ""),
                "tournament_id": tournament.get("id"),
                "tournament_tier": tournament.get("tier", ""),
                "best_of":       match.get("best_of"),
                "won":           is_winner,
                "won_verified":  winner_verified,
                "set_count":     set_count,
                "extended":      extended,
                "retired":       retired,
                "total_games":   total_games,
                "player_games":  player_games,
                "opponent_games": opp_games,
                "player_stats":   player_match_stats,
                "opponent_stats": opponent_match_stats,
                "h2h":              match.get("h2h"),
                "player_odd":       this_player_odd,
                "opponent_odd":     opp_player_odd,
            })

            if len(results) >= limit:
                break

        except Exception as _e:
            continue

    _cache_set(cache_key, results)
    if captured_own_id:
        _cache_set(f"playerid_{canonical_name.lower()}", captured_own_id)
    if captured_own_rank:
        _cache_set(f"playerrank_{canonical_name.lower()}", captured_own_rank)
    log("Matches", f"Fetched {len(results)} recent matches for {canonical_name}"
        + (f" on {surface}" if surface else "")
        + (f" — {uncertain_count} W/L UNCONFIRMED (see debug_raw_match)" if uncertain_count else ""))
    return results


def resolve_player_ranking(canonical_name: str, tour: str = "atp") -> int:
    """
    v7.9.6 (moneyline classification). Ranking wasn't previously
    extracted anywhere in this module despite being present in the
    raw matches-played response (e.g. "currentRank": 7 confirmed in
    the real Gauff JSON pulled earlier this session) — used by
    moneyline's classification proxy (LOPSIDED vs COMPETITIVE).
    Reuses the same proven matches-played path as resolve_player_id().
    """
    cache_key = f"playerrank_{canonical_name.lower()}"
    cached = _cache_get(cache_key, CACHE_TTL.get("search", 30 * 24))
    if cached:
        return cached
    get_recent_matches(canonical_name, limit=1)
    cached = _cache_get(cache_key, CACHE_TTL.get("search", 30 * 24))
    if not cached:
        log("PlayerRank", f"Could not resolve a ranking for {canonical_name} — "
                           f"no matches-played data available.")
    return cached


def resolve_player_id(canonical_name: str, tour: str = "atp") -> int:
    """
    v7.9.6 (moneyline H2H fix). Returns the numeric player ID the API's
    documented v2/{tour}/ endpoints require (h2h/stats, match-stats,
    perf-breakdown all use IDs, not names).

    Deliberately reuses matches-played rather than the vendor's newer
    unified search endpoint (/tennis/v2/search) — that endpoint's
    response shape has never been verified against real data in this
    codebase, while matches-played has been directly confirmed twice
    this session against real, independently-verified match results
    (Gauff/Sierra and Krejcikova/Andreeva). Lower risk to reuse a path
    already proven correct than to integrate an unverified one.
    """
    cache_key = f"playerid_{canonical_name.lower()}"
    cached = _cache_get(cache_key, CACHE_TTL.get("search", 30 * 24))
    if cached:
        return cached

    # Not cached yet — a matches-played call captures and caches the ID
    # as a side effect (see the loop above), then we re-check.
    get_recent_matches(canonical_name, limit=1)
    cached = _cache_get(cache_key, CACHE_TTL.get("search", 30 * 24))
    if not cached:
        log("PlayerID", f"Could not resolve a numeric ID for {canonical_name} — "
                          f"no matches-played data available.")
    return cached


def debug_raw_match(canonical_name: str, opponent_hint: str = None, tour: str = "atp"):
    """
    DIAGNOSTIC ONLY — not called by the pipeline.
    Fetches the raw, uncached matches-played JSON for a player and prints
    the full unparsed object for their most recent match (or the first
    match whose opponent name contains opponent_hint, if given).

    Purpose: we don't have confirmed documentation for what the "draw"
    field encodes or whether "result" lists player1's or the winner's
    games first. Run this against a match whose real-world result you
    already know for certain, then compare the printed fields (draw,
    player1/player2 order, result string) against the known outcome to
    nail down the actual semantics — instead of guessing again.

    Usage:
        python3 openclaw_v8_api.py --debug-match --player "Jack Draper" --opponent "Fritz"
        python3 openclaw_v8_api.py --debug-match --player "Coco Gauff" --tour wta
    """
    # v7.9.6 fix: this originally called matches-played with the raw typed
    # name, skipping the resolve_player_name() step that every other part
    # of the pipeline does first. The API's internal name format doesn't
    # always match how a player is typed (nicknames, suffixes, etc.), so an
    # unresolved lookup can silently return an empty match list rather than
    # an error — which is what happened testing "Coco Gauff" directly.
    resolved = resolve_player_name(canonical_name, tour)
    if not resolved:
        print(f"Could not resolve '{canonical_name}' on tour '{tour}'. "
              f"Try the other tour (--tour wta or --tour atp), or a shorter "
              f"form of the name (e.g. just the last name).")
        return None
    if resolved.lower() != canonical_name.lower():
        print(f"Resolved '{canonical_name}' → API canonical name: '{resolved}'")

    encoded = resolved.replace(" ", "%20")
    data = _api_get(f"profile/{encoded}/matches-played")  # bypasses cache deliberately
    if not data or "singles" not in data:
        print(f"No match data returned for {resolved}")
        return None

    matches = data["singles"]
    target = None
    if opponent_hint:
        hint_lower = opponent_hint.lower()
        for m in matches:
            p1n = m.get("player1", {}).get("name", "").lower()
            p2n = m.get("player2", {}).get("name", "").lower()
            if hint_lower in p1n or hint_lower in p2n:
                target = m
                break
    if target is None and matches:
        target = matches[0]

    if target is None:
        print(f"No matches found for {resolved}" + (f" vs {opponent_hint}" if opponent_hint else ""))
        return None

    print(json.dumps(target, indent=2, default=str))
    return target


def format_recent_matches(player: str, matches: list) -> str:
    """
    Formats recent match list for injection into evaluation prompt.
    """
    if not matches:
        return f"Recent matches for {player}: NOT FOUND via API"

    lines = [f"Recent matches — {player}:"]
    for m in matches:
        if m.get("won") is None:
            result = "RESULT UNCONFIRMED"
        else:
            result = "WIN" if m["won"] else "LOSS"
        ext = " [EXTENDED]" if m["extended"] else ""
        ret = " [RETIRED]" if m["retired"] else ""
        lines.append(
            f"  {m['date']} | {result} vs {m['opponent']} | "
            f"{m['score']} | {m['surface']} | {m['tournament_tier'] or m['tournament']}"
            f"{ext}{ret}"
        )
    return "\n".join(lines)


def get_upcoming_match(canonical_name: str, tour: str = "atp") -> dict:
    """Upcoming match — endpoint not available on this API. Returns empty."""
    return {"found": False}

# ─────────────────────────────────────────────
# DATA TIER
# ─────────────────────────────────────────────

def _data_tier(surface_total: int, hold_pct) -> str:
    if surface_total >= 10 and hold_pct is not None:
        return "Tier1"
    if surface_total >= 5:
        return "Tier2"
    if surface_total > 0:
        return "Tier3"
    return "MISSING"

def _confidence_cap(p_tier: str, o_tier: str) -> int:
    tiers = [p_tier, o_tier]
    if "MISSING" in tiers:
        return 65
    if "Tier3" in tiers:
        return 72
    if "Tier2" in tiers:
        return 80
    return 88

# ─────────────────────────────────────────────
# MAIN PACKET BUILDER
# ─────────────────────────────────────────────

def get_tennis_packet(
    player: str,
    opponent: str,
    surface: str,
    tour: str = "atp"
) -> dict:
    """
    MAIN ENTRY POINT — called by openclaw_core.py before evaluation.

    1. Resolves both player names via API search
    2. Pulls surface records (last 5 years)
    3. Pulls match stats (hold%, break%, aces) for last 3 years
    4. Pulls H2H record on surface
    5. Returns complete data packet with data tier and confidence cap

    Usage in openclaw_core.py:
        from openclaw_v8_api import get_tennis_packet, format_packet_for_eval, v8_validation
        packet = get_tennis_packet(player, opponent, surface, tour)
    """
    surface = surface.lower()
    tour    = tour.lower()

    # Map surface names to API format
    surface_map = {
        "grass": "grass",
        "clay":  "clay",
        "hard":  "hard",
        "indoor hard": "ihard",
        "ihard": "ihard",
    }
    api_surface = surface_map.get(surface, surface)

    log("Packet", f"Building: {player} vs {opponent} | {surface} | {tour.upper()}")

    def _build_stats(name: str) -> dict:
        canonical = resolve_player_name(name, tour)
        if not canonical:
            # v7.9.7 fix (item 5 — silent-failure distinction): previously
            # this fell through to _empty_stats identically whether the
            # player genuinely had no data OR resolve_player_name simply
            # failed to match the name/tour combination given (e.g. a
            # doubles-team string, a wrong tour, a truncated/garbled
            # name). Both looked exactly like "MISSING — no data exists"
            # downstream, with no way to tell them apart. Confirmed via
            # direct API testing: a player (Linda Noskova, WTA) with
            # abundant real V8 data (76.4% hold%, 143 matches) was
            # reported as fully MISSING in a real evaluation — the
            # actual cause was resolution failure, not a genuine data
            # gap, and nothing surfaced that distinction. Logged loudly
            # here specifically so it isn't missed again.
            log("Packet", f"⚠️ RESOLUTION FAILED for '{name}' on tour={tour} — "
                f"treating as no-data, but this may be a name/tour mismatch, "
                f"not a genuine V8 coverage gap. Verify manually if this affects a real pick.")
            return _empty_stats(name, resolution_failed=True)

        surf_data    = get_surface_summary(canonical, api_surface)
        match_data   = get_match_stats(canonical)
        recent       = get_recent_matches(canonical, surface, limit=5)
        recent_surf  = get_recent_matches(canonical, surface, limit=5)

        tier = _data_tier(surf_data.get("total", 0), match_data.get("hold_pct"))

        # Extender detection from recent matches
        extended_matches = [m for m in recent if m.get("extended") and not m.get("retired")]
        extender_confirmed = len(extended_matches) >= 1
        extender_rate = round(len(extended_matches) / len(recent), 2) if recent else None

        # ── AVG DOUBLE FAULTS (v7.9.7) ──────────────────────────
        # No vendor endpoint returns a double-fault AVERAGE directly —
        # get_recent_matches() already captures the per-match doubleFaults
        # count in player_stats (same raw box-score block avg_aces could
        # be built from), but nobody summed it into an average before now.
        # Mirrors avg_aces's approach: sum the per-match values / match
        # count. Unlike total_games, a retirement does NOT invalidate a
        # double-fault count for the games actually played, so retired
        # matches are NOT excluded here — only matches missing the field
        # entirely are skipped.
        df_values = [
            m["player_stats"]["doubleFaults"]
            for m in recent
            if m.get("player_stats", {}).get("doubleFaults") is not None
        ]
        avg_double_faults = (
            round(sum(df_values) / len(df_values), 1) if df_values else None
        )
        df_match_count = len(df_values)

        return {
            "name":              canonical,
            "ranking":           resolve_player_ranking(canonical, tour),
            "surface_record":    surf_data.get("record", "N/A"),
            "surface_wins":      surf_data.get("wins", 0),
            "surface_losses":    surf_data.get("losses", 0),
            "surface_total":     surf_data.get("total", 0),
            "win_pct":           surf_data.get("win_pct"),
            "hold_pct":          match_data.get("hold_pct"),
            "break_pct":         match_data.get("break_pct"),
            "avg_aces":          match_data.get("avg_aces"),
            "avg_double_faults": avg_double_faults,
            "df_match_count":    df_match_count,
            "first_serve":       match_data.get("first_serve"),
            "bp_save_pct":       match_data.get("bp_save_pct"),
            "matches_in_stats":  match_data.get("matches", 0),
            "data_tier":         tier,
            "recent_matches":    recent,
            "extender_confirmed": extender_confirmed,
            "extender_rate":     extender_rate,
            "resolution_failed": False,
        }

    def _empty_stats(name: str, resolution_failed: bool = False) -> dict:
        # v7.9.7 fix (item 5): resolution_failed distinguishes "name/tour
        # lookup itself failed" (True) from "resolved fine, but this
        # player genuinely has zero confirmed data" (False, the original
        # behavior for a real but data-poor player). Callers (research_agent,
        # moneyline evaluation, compliance) can use this to flag a real
        # resolution problem distinctly from an honest data gap, instead
        # of both being reported identically as "MISSING".
        return {
            "name":               name,
            "surface_record":     "N/A",
            "surface_wins":       0,
            "surface_losses":     0,
            "surface_total":      0,
            "win_pct":            None,
            "hold_pct":           None,
            "break_pct":          None,
            "avg_aces":           None,
            "avg_double_faults":  None,
            "df_match_count":     0,
            "first_serve":        None,
            "bp_save_pct":        None,
            "matches_in_stats":   0,
            "data_tier":          "MISSING",
            "recent_matches":     [],
            "extender_confirmed": False,
            "extender_rate":      None,
            "resolution_failed":  resolution_failed,
        }

    p_stats = _build_stats(player)
    o_stats = _build_stats(opponent)

    # H2H
    # v7.9.6 fix: the old condition `p_stats["name"] != player` was backwards.
    # _empty_stats() sets "name" to the raw input string on resolution
    # FAILURE, so this check was actually True whenever resolution SUCCEEDED
    # (since the canonical name almost always differs from the raw typed
    # name) — meaning H2H was skipped in the common case and only attempted
    # when resolution had failed.
    #
    # v7.9.7 fix (item 5): now uses the precise resolution_failed flag
    # instead of data_tier == "MISSING". The old data_tier check was only
    # approximate — a player who resolved successfully but genuinely has
    # thin/no stats ALSO gets data_tier "MISSING", identical to an actual
    # resolution failure. That conflation was the same root problem
    # documented in _build_stats above. resolution_failed distinguishes
    # them directly, so H2H is now skipped only when a name genuinely
    # couldn't be resolved, not whenever a real player simply has thin data.
    name_resolution_failed = (
        p_stats.get("resolution_failed", False) or o_stats.get("resolution_failed", False)
    )
    if name_resolution_failed:
        h2h = {
            "status": "UNAVAILABLE",
            "p1_wins": None, "p2_wins": None,
            "p1_surface_wins": None, "p2_surface_wins": None, "total": None,
        }
    else:
        h2h = get_h2h(p_stats["name"], o_stats["name"], surface, tour)

    p_tier = p_stats["data_tier"]
    o_tier = o_stats["data_tier"]
    cap    = _confidence_cap(p_tier, o_tier)

    return {
        "player":       p_stats,
        "opponent":     o_stats,
        "h2h":          h2h,
        "surface":      surface,
        "tour":         tour,
        "data_quality": {
            "player_tier":    p_tier,
            "opponent_tier":  o_tier,
            "confidence_cap": cap,
        }
    }

# ─────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────

HOLD_DEPENDENT = ["total_games", "games_won"]

def v8_validation(player: str, opponent: str, prop_type: str, packet: dict) -> dict:
    """
    Runs after get_tennis_packet().
    Returns validation status, missing fields, warnings, and confidence cap.
    """
    missing  = []
    warnings = []
    p  = packet["player"]
    o  = packet["opponent"]
    dq = packet["data_quality"]

    if p["surface_record"] == "N/A":
        missing.append(f"{player} — no surface record found")
    if o["surface_record"] == "N/A":
        missing.append(f"{opponent} — no surface record found")

    if prop_type in HOLD_DEPENDENT:
        if p["hold_pct"] is None:
            warnings.append(f"{player} hold% not confirmed — research agent must verify")
        if o["hold_pct"] is None:
            warnings.append(f"{opponent} hold% not confirmed — research agent must verify")

    if "MISSING" in [dq["player_tier"], dq["opponent_tier"]]:
        warnings.append(
            f"One or both players have no surface data — "
            f"confidence capped at {dq['confidence_cap']}%"
        )

    h2h = packet.get("h2h", {})
    if h2h.get("status") != "CONFIRMED":
        warnings.append(
            "H2H UNAVAILABLE (v7.9.6) — endpoint failed or returned no data. "
            "Do not treat as a confirmed 0-0 record; H2H simply cannot be "
            "used as a structural fact for this evaluation."
        )

    return {
        "status":         "INCOMPLETE" if missing else "APPROVED",
        "missing":        missing,
        "warnings":       warnings,
        "confidence_cap": dq["confidence_cap"],
        "player_tier":    dq["player_tier"],
        "opponent_tier":  dq["opponent_tier"],
    }

# ─────────────────────────────────────────────
# FORMAT FOR EVALUATION PROMPT
# ─────────────────────────────────────────────

def format_packet_for_eval(player: str, opponent: str, packet: dict) -> str:
    """
    Returns formatted string injected into evaluation agent prompt
    before the 12 checkpoints run.
    """
    p    = packet["player"]
    o    = packet["opponent"]
    h    = packet.get("h2h", {})
    dq   = packet["data_quality"]
    surf = packet["surface"].capitalize()

    def fmt(val, suffix="", na="N/A"):
        return f"{val}{suffix}" if val is not None else na

    lines = [
        f"═══ V8 CONFIRMED DATA (Tennis Stats API) ═══",
        f"Surface: {surf} | Tour: {packet['tour'].upper()}",
        f"",
        f"── {p['name']} ──",
        f"  Surface Record ({surf}, last 5yr): {p['surface_record']} ({p['surface_total']} matches)",
        f"  Win %:          {fmt(p['win_pct'], '%')}",
        f"  Hold %:         {fmt(p['hold_pct'], '%')}  [Data: {p['data_tier']}]",
        f"  Break %:        {fmt(p['break_pct'], '%')}",
        f"  BP Save %:      {fmt(p['bp_save_pct'], '%')}",
        f"  Avg Aces:       {fmt(p['avg_aces'])}",
        f"  Avg Double Faults: {fmt(p['avg_double_faults'])}"
        + (f"  (from {p['df_match_count']} matches)" if p.get('df_match_count') else ""),
        f"  First Serve %:  {fmt(p['first_serve'], '%')}",
        f"  Extender (REQ1): {'✅ CONFIRMED' if p.get('extender_confirmed') else '❌ NOT CONFIRMED'}"
        + (f" ({int(p['extender_rate']*100)}% of recent matches extended)" if p.get('extender_rate') else ""),
    ]

    # Recent matches for player
    if p.get("recent_matches"):
        lines.append(f"")
        lines.append(f"  Recent matches:")
        for m in p["recent_matches"][:5]:
            result = "?" if m.get("won") is None else ("W" if m["won"] else "L")
            ext = " [EXT]" if m["extended"] else ""
            ret = " [RET]" if m["retired"] else ""
            lines.append(
                f"    {m['date']} {result} vs {m['opponent']} "
                f"{m['score']} ({m['surface']}){ext}{ret}"
            )

    lines += [
        f"",
        f"── {o['name']} ──",
        f"  Surface Record ({surf}, last 5yr): {o['surface_record']} ({o['surface_total']} matches)",
        f"  Win %:          {fmt(o['win_pct'], '%')}",
        f"  Hold %:         {fmt(o['hold_pct'], '%')}  [Data: {o['data_tier']}]",
        f"  Break %:        {fmt(o['break_pct'], '%')}",
        f"  BP Save %:      {fmt(o['bp_save_pct'], '%')}",
        f"  Avg Aces:       {fmt(o['avg_aces'])}",
        f"  Avg Double Faults: {fmt(o['avg_double_faults'])}"
        + (f"  (from {o['df_match_count']} matches)" if o.get('df_match_count') else ""),
        f"  First Serve %:  {fmt(o['first_serve'], '%')}",
        f"  Extender (REQ1): {'✅ CONFIRMED' if o.get('extender_confirmed') else '❌ NOT CONFIRMED'}"
        + (f" ({int(o['extender_rate']*100)}% of recent matches extended)" if o.get('extender_rate') else ""),
    ]

    # Recent matches for opponent
    if o.get("recent_matches"):
        lines.append(f"")
        lines.append(f"  Recent matches:")
        for m in o["recent_matches"][:5]:
            result = "?" if m.get("won") is None else ("W" if m["won"] else "L")
            ext = " [EXT]" if m["extended"] else ""
            ret = " [RET]" if m["retired"] else ""
            lines.append(
                f"    {m['date']} {result} vs {m['opponent']} "
                f"{m['score']} ({m['surface']}){ext}{ret}"
            )

    h2h_line1 = (
        f"  Overall:   UNAVAILABLE — h2h endpoint failed, do NOT treat as 0-0"
        if h.get("status") != "CONFIRMED"
        else f"  Overall:   {p['name']} {h.get('p1_wins',0)}-{h.get('p2_wins',0)} {o['name']}"
    )
    h2h_line2 = (
        f"  On {surf}: UNAVAILABLE — h2h endpoint failed, do NOT treat as 0-0"
        if h.get("status") != "CONFIRMED"
        else f"  On {surf}: {p['name']} {h.get('p1_surface_wins',0)}-{h.get('p2_surface_wins',0)} {o['name']}"
    )

    lines += [
        f"",
        f"── H2H ──",
        h2h_line1,
        h2h_line2,
        f"",
        f"Data Quality: Player={dq['player_tier']} | Opponent={dq['opponent_tier']}",
        f"Confidence Cap: {dq['confidence_cap']}%",
        f"",
        f"REQ1 STATUS:",
        f"  {p['name']}: {'✅ EXTENDER CONFIRMED' if p.get('extender_confirmed') else '❌ NOT CONFIRMED — check recent matches above'}",
        f"  {o['name']}: {'✅ EXTENDER CONFIRMED' if o.get('extender_confirmed') else '❌ NOT CONFIRMED — check recent matches above'}",
        f"  Both confirmed: {'✅ REQ1 MET' if p.get('extender_confirmed') and o.get('extender_confirmed') else '⚠️ REQ1 INCOMPLETE — MORE path capped at YELLOW'}",
        f"",
        f"DIRECTION DISCIPLINE: Hold% and surface record above are",
        f"confirmed structural facts. These SET direction.",
        f"Narratives, volatility flags, and previews may only",
        f"ADJUST confidence — never set or flip direction.",
        f"",
        f"RECENT MATCH W/L: derived from the confirmed scoreline order",
        f"(player1's games listed first in every set), verified against",
        f"two independently-confirmed real Wimbledon 2026 results. A result",
        f"of \"?\" above means this specific match's score could not be",
        f"parsed (e.g. retirement/walkover) — treat only that match as",
        f"unconfirmed; all other W/L results above are confirmed.",
        f"═══════════════════════════════════════════════════",
    ]
    return "\n".join(lines)

# ─────────────────────────────────────────────
# SELFTEST
# ─────────────────────────────────────────────

def run_selftest():
    log("Selftest", "=== V8 Tennis Stats API Selftest ===")

    log("Selftest", "Test 1: Djokovic vs Alcaraz | grass | ATP")
    packet = get_tennis_packet("Novak Djokovic", "Carlos Alcaraz", "grass", "atp")
    print("\n" + format_packet_for_eval("Novak Djokovic", "Carlos Alcaraz", packet))

    v = v8_validation("Novak Djokovic", "Carlos Alcaraz", "total_games", packet)
    log("Selftest", f"Validation: {v['status']} | Cap: {v['confidence_cap']}%")
    log("Selftest", f"Player tier: {v['player_tier']} | Opponent tier: {v['opponent_tier']}")
    if v["warnings"]:
        for w in v["warnings"]:
            log("Selftest", f"Warning: {w}")

    log("Selftest", "")
    log("Selftest", "Test 2: Swiatek vs Sabalenka | clay | WTA")
    packet2 = get_tennis_packet("Iga Swiatek", "Aryna Sabalenka", "clay", "wta")
    print("\n" + format_packet_for_eval("Iga Swiatek", "Aryna Sabalenka", packet2))

    log("Selftest", "")
    log("Selftest", "Test 3: Single player hold% calculation")
    log("Selftest", "Expected Djokovic 2025 hold% ~88-89%")
    p_stats = get_match_stats("Novak Djokovic", [2025])
    log("Selftest", f"Djokovic 2025 hold%: {p_stats.get('hold_pct')}%")
    log("Selftest", f"Djokovic 2025 avg aces: {p_stats.get('avg_aces')}")
    log("Selftest", f"Djokovic 2025 break%: {p_stats.get('break_pct')}%")
    log("Selftest", f"Djokovic 2025 first serve%: {p_stats.get('first_serve')}%")

    log("Selftest", "")
    log("Selftest", "=== ALL SELFTESTS COMPLETE ===")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OpenClaw V8 Tennis Stats API")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--player",   type=str, help="Test single player")
    parser.add_argument("--tour",     type=str, default="atp")
    parser.add_argument("--surface",  type=str, default="grass")
    parser.add_argument("--opponent", type=str, default="")
    parser.add_argument("--debug-match", action="store_true",
        help="Dump raw JSON for one match to diagnose draw/result field "
             "semantics — use with --player and optionally --opponent")
    args = parser.parse_args()

    if args.debug_match:
        if not args.player:
            print("--debug-match requires --player \"Name\"")
        else:
            debug_raw_match(args.player, args.opponent or None, args.tour)
    elif args.selftest:
        run_selftest()
    elif args.player:
        if args.opponent:
            packet = get_tennis_packet(args.player, args.opponent, args.surface, args.tour)
            print(format_packet_for_eval(args.player, args.opponent, packet))
        else:
            canonical = resolve_player_name(args.player, args.tour)
            print(f"Canonical name: {canonical}")
            if canonical:
                stats = get_match_stats(canonical)
                surf  = get_surface_summary(canonical, args.surface)
                print(f"Surface record ({args.surface}): {surf}")
                print(f"Hold%: {stats.get('hold_pct')}%")
                print(f"Break%: {stats.get('break_pct')}%")
                print(f"Avg aces: {stats.get('avg_aces')}")
    else:
        parser.print_help()
