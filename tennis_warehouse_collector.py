#!/usr/bin/env python3
"""
TENNIS_WAREHOUSE_COLLECTOR.PY — Dataset B Backfill + Forward Collector
Approved architecture: completely separate from openclaw_core.py and
Dataset A. Imports ONLY openclaw_v8_api.py directly -- no dependency on
the bot's own process, so a collector bug can never touch evaluations.

USAGE (on the Mac Mini, from the project root):
    python3 tennis_warehouse_collector.py --backfill        # 2020-2026, seed list
    python3 tennis_warehouse_collector.py --forward         # today only, known players

Every run is logged to collector_runs per the approved 10-field audit
requirement, and collector_heartbeat is updated throughout so Mission
Control can show live status once Warehouse Command Center exists.
"""
import argparse
import json
import sqlite3
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    import openclaw_v8_api as v8
except Exception as e:
    print(f"FATAL: cannot import openclaw_v8_api from {HERE}: {e}")
    sys.exit(1)

DB_PATH = HERE / "data" / "warehouse" / "tennis_warehouse.sqlite"
SCHEMA_PATH = HERE / "tennis_warehouse_schema.sql"
SEED_PATH = HERE / "player_seed_list_starter.json"
BACKFILL_YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _heartbeat(conn, status, current_task=None, inc_success=False, inc_failure=False):
    """Single-row upsert, same pattern as Dataset A's agent_heartbeat
    but in THIS file's own table -- total isolation, per the approved
    architecture."""
    row = conn.execute("SELECT success_count, failure_count FROM collector_heartbeat WHERE id=1").fetchone()
    succ, fail = (row if row else (0, 0))
    if inc_success: succ += 1
    if inc_failure: fail += 1
    conn.execute("""
        INSERT INTO collector_heartbeat (id, status, current_task, last_heartbeat, success_count, failure_count)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status, current_task=excluded.current_task,
            last_heartbeat=excluded.last_heartbeat,
            success_count=excluded.success_count, failure_count=excluded.failure_count
    """, (status, current_task, _now(), succ, fail))
    conn.commit()


def _match_id(player_a, player_b, match_date):
    pair = sorted([player_a.lower().strip(), player_b.lower().strip()])
    return f"{pair[0]}|{pair[1]}|{match_date}"


def _load_seed_players():
    """Reads the starter seed file into the player_seed_list table if
    it isn't there yet, then returns every known player (seed + any
    prior auto-growth) for this run to process."""
    conn = sqlite3.connect(DB_PATH)
    existing = conn.execute("SELECT COUNT(*) FROM player_seed_list").fetchone()[0]
    if existing == 0 and SEED_PATH.exists():
        seed = json.loads(SEED_PATH.read_text())
        for tour_key, tour_label in (("atp", "ATP"), ("wta", "WTA")):
            for p in seed.get(tour_key, []):
                conn.execute("""
                    INSERT OR IGNORE INTO player_seed_list (player, tour, ranking_at_seed, source, added_at)
                    VALUES (?,?,?,?,?)
                """, (p["name"], tour_label, p.get("rank"), "MANUAL_SEED", _now()))
        conn.commit()
    players = conn.execute("SELECT player, tour FROM player_seed_list").fetchall()
    conn.close()
    return players


def _strip_accents(s):
    """NFKD-normalize and drop combining marks -- 'Carreño' -> 'Carreno'."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _name_variants(player_name):
    """
    Generates plausible alternate spellings/orderings for a player name,
    tried in order against v8.resolve_player_name() when the original
    fails. This does NOT change what "canonical" means or touch
    openclaw_v8_api.py's own matching logic at all -- v8.resolve_player_name()
    remains the sole source of truth for that. This only gives it more
    chances to succeed by varying the INPUT shape, on the working theory
    from the original handoff (accent / name-order mismatches) for the
    3 known exceptions (Pablo Carreño Busta, Victor Estrella Burgos,
    Zhang Shuai) -- none of which are hardcoded here, since the fix
    should generalize to any future player hitting the same pattern,
    not just these 3 by name.

    Order matters: player_name itself is always tried first, so a name
    that already resolves correctly today is completely unaffected --
    the loop breaks on the first success.
    """
    variants = []
    seen = set()

    def add(v):
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            variants.append(v)

    add(player_name)                      # original -- always tried first
    stripped = _strip_accents(player_name)
    add(stripped)                          # accent-stripped

    # Name-order swap ("Last First" <-> "First Last") -- covers the
    # Zhang Shuai case if the seed list and V8's expected order differ,
    # and any two-token accent case too (tried on both spellings).
    for base in (player_name, stripped):
        parts = base.split()
        if len(parts) == 2:
            add(f"{parts[1]} {parts[0]}")

    # Compound-surname join, for 3-token names ("Estrella Burgos" as a
    # single two-word surname vs. three independent tokens) -- covers
    # the Victor Estrella Burgos case if V8 expects the surname joined
    # or split differently than the seed list has it.
    for base in (player_name, stripped):
        parts = base.split()
        if len(parts) == 3:
            add(f"{parts[0]} {parts[1]}{parts[2]}")   # First LastLast
            add(f"{parts[0]}{parts[1]} {parts[2]}")   # FirstLast Last

    return variants


def collect_player(conn, player_name, tour, years, run_stats):
    """Pulls year-level aggregate stats and recent per-match records
    for one player, writes both, returns nothing -- mutates run_stats
    in place (rows_added/updated/failed, api_errors, warnings)."""
    tour_lower = tour.lower()
    canonical = None
    resolved_via = None
    variants = _name_variants(player_name)
    for variant in variants:
        canonical = v8.resolve_player_name(variant, tour_lower)
        if canonical:
            resolved_via = variant
            break
    if not canonical:
        run_stats["warnings"].append(
            f"Could not resolve: {player_name} ({tour}) — tried {len(variants)} "
            f"name variant(s): {variants}")
        run_stats["rows_failed"] += 1
        return
    if resolved_via != player_name:
        # Not an error -- record it so it's visible which players are
        # relying on a variant match, rather than silently succeeding
        # with no trace of why the original input didn't work.
        run_stats.setdefault("resolved_via_variant", []).append(
            f"{player_name} -> matched via '{resolved_via}'")

    now = _now()

    # ── player identity + current ranking ──────────────────────────
    try:
        ranking = v8.resolve_player_ranking(canonical, tour_lower)
    except Exception as e:
        ranking = None
        run_stats["warnings"].append(f"Ranking lookup failed for {canonical}: {e}")

    existing = conn.execute("SELECT player FROM players WHERE player=?", (canonical,)).fetchone()
    conn.execute("""
        INSERT INTO players (player, tour, current_ranking, ranking_updated_at, first_seen_at, last_seen_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(player) DO UPDATE SET
            current_ranking=excluded.current_ranking,
            ranking_updated_at=excluded.ranking_updated_at,
            last_seen_at=excluded.last_seen_at
    """, (canonical, tour, ranking, now, now, now))
    run_stats["rows_updated" if existing else "rows_added"] += 1

    if ranking is not None:
        conn.execute("""
            INSERT OR IGNORE INTO rankings_snapshot (player, snapshot_date, ranking, tour)
            VALUES (?,?,?,?)
        """, (canonical, now[:10], ranking, tour))
        run_stats["rows_added"] += 1

    # ── year-level aggregate stats (the REAL granularity, per the
    # schema correction) ─────────────────────────────────────────────
    for year in years:
        try:
            stats = v8.get_match_stats(canonical, years=[year])
        except Exception as e:
            run_stats["api_errors"].append(f"{canonical} {year}: {e}")
            run_stats["rows_failed"] += 1
            continue
        if not stats or stats.get("matches", 0) == 0:
            continue  # no data that year -- not an error, just nothing to write

        data_source = "FALLBACK_PARTIAL" if stats.get("_source") == "matches_played_fallback" else "AGGREGATE"

        # Bug fix (confirmed via real Nadal evidence -- 2025/2026 both
        # showing identical matches_count=10, both FALLBACK_PARTIAL):
        # _fallback_stats_from_matches_played() has no year parameter --
        # it returns whatever's currently on file from the rolling
        # matches-played endpoint, completely blind to which year
        # get_match_stats() was originally asked about. Requesting the
        # same player across multiple years that all fall back returns
        # the SAME underlying data every time, previously written under
        # every requested year as if it belonged there. Scope confirmed:
        # 59 of 197 players, 225 rows, 17.1% of player_year_stats.
        #
        # Fix: the fallback dict already includes
        # "_most_recent_match_date" (confirmed present, just never
        # checked before). Only accept a FALLBACK_PARTIAL row if that
        # date's year actually matches the year being requested. If it
        # doesn't -- skip entirely. No estimation, no relabeling, no
        # fabricated season, per the approved fix direction.
        if data_source == "FALLBACK_PARTIAL":
            most_recent = stats.get("_most_recent_match_date")
            most_recent_year = None
            if most_recent:
                try:
                    most_recent_year = int(str(most_recent)[:4])
                except (ValueError, TypeError):
                    most_recent_year = None
            if most_recent_year != year:
                run_stats["warnings"].append(
                    f"{canonical} {year}: FALLBACK_PARTIAL rejected -- "
                    f"most recent match on file is from {most_recent_year or 'unknown'}, "
                    f"not {year}. Row skipped, not written.")
                continue

        already = conn.execute(
            "SELECT player FROM player_year_stats WHERE player=? AND year=?", (canonical, year)).fetchone()
        conn.execute("""
            INSERT INTO player_year_stats
                (player, year, tour, hold_pct, break_pct, avg_aces, first_serve_pct,
                 bp_save_pct, matches_count, data_source, collected_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(player, year) DO UPDATE SET
                hold_pct=excluded.hold_pct, break_pct=excluded.break_pct,
                avg_aces=excluded.avg_aces, first_serve_pct=excluded.first_serve_pct,
                bp_save_pct=excluded.bp_save_pct, matches_count=excluded.matches_count,
                data_source=excluded.data_source, collected_at=excluded.collected_at
        """, (canonical, year, tour, stats.get("hold_pct"), stats.get("break_pct"),
              stats.get("avg_aces"), stats.get("first_serve"), stats.get("bp_save_pct"),
              stats.get("matches"), data_source, now))
        run_stats["rows_updated" if already else "rows_added"] += 1

    # ── recent per-match records (real per-match fields, separate
    # table per the schema correction) ───────────────────────────────
    try:
        recent = v8.get_recent_matches(canonical, limit=20)
    except Exception as e:
        run_stats["api_errors"].append(f"{canonical} recent matches: {e}")
        recent = []

    for m in recent:
        opponent = m.get("opponent")
        match_date = m.get("date")
        if not opponent or not match_date:
            continue
        mid = _match_id(canonical, opponent, match_date)
        # Bug fix (caught from real data, not the mock): the actual key
        # get_recent_matches() uses is "won", not "winner" -- confirmed
        # directly from the real function body (line ~1413:
        # results.append({..., "won": is_winner, "won_verified":
        # winner_verified, ...})). "winner" doesn't exist anywhere in
        # the returned dict, which is why every match wrote NULL --
        # m.get("winner") was always None regardless of the real result.
        # Also now respecting won_verified explicitly: if the vendor's
        # score string couldn't be parsed (retirement/walkover/bad
        # data), won_verified is False and winner stays NULL even if
        # `won` happens to hold a stale/default value -- same
        # don't-guess standard the rest of this file already follows.
        won = m.get("won") if m.get("won_verified") else None
        winner_name = canonical if won is True else (opponent if won is False else None)

        # Real per-match box score, confirmed available (player_stats/
        # opponent_stats). v7.9.8: get_recent_matches() has always
        # returned the FULL raw stats block here (not narrowed) -- this
        # file was the one only reading 4 of the ~18 available fields.
        # Widened below to capture the approved additional fields
        # (unforcedErrors, winners, breakPointsConverted/Of,
        # winningOnFirstServe/Of, winningOnSecondServe/Of, fastestServe,
        # averageFirstServeSpeed/Of), plus h2h, tournament_id, best_of,
        # total_games/player_games/opponent_games, and per-player
        # historical odd -- all confirmed present in the real API
        # response and now confirmed passed through by get_recent_matches().
        p_stats = m.get("player_stats") or {}
        o_stats = m.get("opponent_stats") or {}

        already = conn.execute("SELECT match_id FROM matches WHERE match_id=?", (mid,)).fetchone()
        conn.execute("""
            INSERT INTO matches
                (match_id, tour, player_a, player_b, winner, surface, tournament,
                 match_date, score, set_count, extended, retired,
                 player_a_aces, player_a_double_faults, player_a_first_serve_in, player_a_first_serve_of,
                 player_b_aces, player_b_double_faults, player_b_first_serve_in, player_b_first_serve_of,
                 player_a_unforced_errors, player_a_winners,
                 player_a_break_points_converted, player_a_break_points_converted_of,
                 player_a_winning_first_serve, player_a_winning_first_serve_of,
                 player_a_winning_second_serve, player_a_winning_second_serve_of,
                 player_a_fastest_serve, player_a_avg_first_serve_speed, player_a_avg_second_serve_speed,
                 player_b_unforced_errors, player_b_winners,
                 player_b_break_points_converted, player_b_break_points_converted_of,
                 player_b_winning_first_serve, player_b_winning_first_serve_of,
                 player_b_winning_second_serve, player_b_winning_second_serve_of,
                 player_b_fastest_serve, player_b_avg_first_serve_speed, player_b_avg_second_serve_speed,
                 h2h_at_match, tournament_id, best_of,
                 total_games, player_a_games, player_b_games,
                 player_a_odd, player_b_odd,
                 source, collected_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,
                    ?,?)
            ON CONFLICT(match_id) DO UPDATE SET
                winner=excluded.winner, score=excluded.score, collected_at=excluded.collected_at,
                player_a_aces=excluded.player_a_aces, player_a_double_faults=excluded.player_a_double_faults,
                player_a_first_serve_in=excluded.player_a_first_serve_in, player_a_first_serve_of=excluded.player_a_first_serve_of,
                player_b_aces=excluded.player_b_aces, player_b_double_faults=excluded.player_b_double_faults,
                player_b_first_serve_in=excluded.player_b_first_serve_in, player_b_first_serve_of=excluded.player_b_first_serve_of,
                player_a_unforced_errors=excluded.player_a_unforced_errors, player_a_winners=excluded.player_a_winners,
                player_a_break_points_converted=excluded.player_a_break_points_converted,
                player_a_break_points_converted_of=excluded.player_a_break_points_converted_of,
                player_a_winning_first_serve=excluded.player_a_winning_first_serve,
                player_a_winning_first_serve_of=excluded.player_a_winning_first_serve_of,
                player_a_winning_second_serve=excluded.player_a_winning_second_serve,
                player_a_winning_second_serve_of=excluded.player_a_winning_second_serve_of,
                player_a_fastest_serve=excluded.player_a_fastest_serve,
                player_a_avg_first_serve_speed=excluded.player_a_avg_first_serve_speed,
                player_a_avg_second_serve_speed=excluded.player_a_avg_second_serve_speed,
                player_b_unforced_errors=excluded.player_b_unforced_errors, player_b_winners=excluded.player_b_winners,
                player_b_break_points_converted=excluded.player_b_break_points_converted,
                player_b_break_points_converted_of=excluded.player_b_break_points_converted_of,
                player_b_winning_first_serve=excluded.player_b_winning_first_serve,
                player_b_winning_first_serve_of=excluded.player_b_winning_first_serve_of,
                player_b_winning_second_serve=excluded.player_b_winning_second_serve,
                player_b_winning_second_serve_of=excluded.player_b_winning_second_serve_of,
                player_b_fastest_serve=excluded.player_b_fastest_serve,
                player_b_avg_first_serve_speed=excluded.player_b_avg_first_serve_speed,
                player_b_avg_second_serve_speed=excluded.player_b_avg_second_serve_speed,
                h2h_at_match=excluded.h2h_at_match, tournament_id=excluded.tournament_id, best_of=excluded.best_of,
                total_games=excluded.total_games, player_a_games=excluded.player_a_games, player_b_games=excluded.player_b_games,
                player_a_odd=excluded.player_a_odd, player_b_odd=excluded.player_b_odd
        """, (mid, tour, canonical, opponent, winner_name, m.get("surface"),
              m.get("tournament"), match_date, m.get("score"), m.get("set_count"),
              int(bool(m.get("extended"))), int(bool(m.get("retired"))),
              p_stats.get("aces"), p_stats.get("doubleFaults"),
              p_stats.get("firstServe"), p_stats.get("firstServeOf"),
              o_stats.get("aces"), o_stats.get("doubleFaults"),
              o_stats.get("firstServe"), o_stats.get("firstServeOf"),
              p_stats.get("unforcedErrors"), p_stats.get("winners"),
              p_stats.get("breakPointsConverted"), p_stats.get("breakPointsConvertedOf"),
              p_stats.get("winningOnFirstServe"), p_stats.get("winningOnFirstServeOf"),
              p_stats.get("winningOnSecondServe"), p_stats.get("winningOnSecondServeOf"),
              p_stats.get("fastestServe"), p_stats.get("averageFirstServeSpeed"), p_stats.get("averageSecondServeSpeed"),
              o_stats.get("unforcedErrors"), o_stats.get("winners"),
              o_stats.get("breakPointsConverted"), o_stats.get("breakPointsConvertedOf"),
              o_stats.get("winningOnFirstServe"), o_stats.get("winningOnFirstServeOf"),
              o_stats.get("winningOnSecondServe"), o_stats.get("winningOnSecondServeOf"),
              o_stats.get("fastestServe"), o_stats.get("averageFirstServeSpeed"), o_stats.get("averageSecondServeSpeed"),
              m.get("h2h"), m.get("tournament_id"), m.get("best_of"),
              m.get("total_games"), m.get("player_games"), m.get("opponent_games"),
              m.get("player_odd"), m.get("opponent_odd"),
              "V8", now))
        run_stats["rows_updated" if already else "rows_added"] += 1


def run(mode):
    run_id = str(uuid.uuid4())
    started_at = _now()
    t0 = time.time()
    run_stats = {"rows_added": 0, "rows_updated": 0, "rows_failed": 0,
                 "api_errors": [], "warnings": [], "resolved_via_variant": []}

    conn = _ensure_db()
    _heartbeat(conn, "WORKING", current_task=f"{mode} run starting")

    players = _load_seed_players()
    years = BACKFILL_YEARS if mode == "backfill" else [datetime.now().year]

    status = "SUCCESS"
    try:
        for player_name, tour in players:
            _heartbeat(conn, "WORKING", current_task=f"{player_name} ({tour})")
            try:
                collect_player(conn, player_name, tour, years, run_stats)
                conn.commit()
            except Exception as e:
                run_stats["api_errors"].append(f"{player_name}: {e}")
                run_stats["rows_failed"] += 1
    except Exception as e:
        status = "FAILED"
        run_stats["api_errors"].append(f"Run-level failure: {e}")

    if run_stats["rows_failed"] > 0 and status == "SUCCESS":
        status = "PARTIAL"

    # Name-variant-retry visibility (this session): folded into the
    # existing warnings column rather than a new one, since
    # tennis_warehouse_schema.sql wasn't available to safely alter --
    # prefixed so it reads distinctly from a real warning on review.
    _persisted_warnings = list(run_stats["warnings"]) + [
        f"[variant-resolved] {v}" for v in run_stats["resolved_via_variant"]]

    finished_at = _now()
    duration = round(time.time() - t0)
    warehouse_size = sum(
        conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("matches", "player_year_stats", "players", "rankings_snapshot")
    )

    conn.execute("""
        INSERT INTO collector_runs
            (run_id, source, started_at, finished_at, duration_sec, rows_added,
             rows_updated, rows_failed, api_errors, warnings, status, warehouse_size_after)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (run_id, "V8", started_at, finished_at, duration,
          run_stats["rows_added"], run_stats["rows_updated"], run_stats["rows_failed"],
          json.dumps(run_stats["api_errors"]), json.dumps(_persisted_warnings),
          status, warehouse_size))
    conn.commit()

    _heartbeat(conn, "IDLE" if status != "FAILED" else "FAILED",
               current_task=None,
               inc_success=(status != "FAILED"), inc_failure=(status == "FAILED"))

    print(f"COLLECTOR RUN {run_id[:8]}")
    print(f"  Source: V8")
    print(f"  Rows Added: {run_stats['rows_added']}")
    print(f"  Rows Updated: {run_stats['rows_updated']}")
    print(f"  Rows Failed: {run_stats['rows_failed']}")
    print(f"  Duration: {duration}s")
    print(f"  Status: {status}")
    print(f"  Warehouse size after run: {warehouse_size}")
    if run_stats["api_errors"]:
        print(f"  API errors: {run_stats['api_errors']}")
    if run_stats["warnings"]:
        print(f"  Warnings: {run_stats['warnings']}")
    if run_stats["resolved_via_variant"]:
        print(f"  Resolved via name variant ({len(run_stats['resolved_via_variant'])}):")
        for v in run_stats["resolved_via_variant"]:
            print(f"    {v}")

    conn.close()
    return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="Collect 2020-2026 for all seed players")
    parser.add_argument("--forward", action="store_true", help="Collect current year only")
    args = parser.parse_args()

    if args.backfill:
        run("backfill")
    elif args.forward:
        run("forward")
    else:
        print("Specify --backfill or --forward")
        sys.exit(1)

# © 2026 Demetrius Reynolds — Project Apex Intelligence™. All rights reserved.
