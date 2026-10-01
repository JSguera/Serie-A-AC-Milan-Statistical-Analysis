"""Download Lega Serie A team-scoped season and match statistics."""

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


API_ROOT = "https://api-sdp.legaseriea.it/v1/serie-a/football"
COMPETITION_ID = "serie-a::Football_Competition::ec93b94f74294dc98ab5bcfd67fc0d88"


class LegaClient:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.last_request = 0.0

    def get(self, path: str) -> dict:
        wait = self.delay - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)

        url = f"{API_ROOT}/{path}?{urlencode({'locale': 'it-IT'})}"
        request = Request(
            url,
            headers={"User-Agent": "SerieAResearchDataCollector/1.0"},
        )
        last_error = None
        for attempt in range(3):
            self.last_request = time.monotonic()
            try:
                with urlopen(request, timeout=30) as response:
                    return json.load(response)
            except (HTTPError, URLError, TimeoutError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"Request failed for {url}: {last_error}")


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "team"


def normalize_team_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9]", "", value.casefold())
    return name[2:] if name.startswith("ac") else name


def team_names(team: dict) -> set[str]:
    names = {
        team.get(key, "")
        for key in ("shortName", "officialName", "mediaName", "acronymName")
    }
    return {normalize_team_name(name) for name in names if name}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], base_fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metric_fields = sorted({key for row in rows for key in row} - set(base_fields))
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=base_fields + metric_fields)
        writer.writeheader()
        writer.writerows(rows)


def roster_row(player: dict) -> dict:
    info = {
        item.get("infoId"): item.get("infoValue")
        for item in player.get("info", [])
    }
    return {
        "player_id": player.get("playerId"),
        "provider_player_id": player.get("providerId"),
        "name": player.get("displayName"),
        "shirt_name": player.get("shirtName"),
        "first_name": player.get("mediaFirstName"),
        "last_name": player.get("mediaLastName"),
        "shirt_number": player.get("bibNumber") or info.get("jersey"),
        "position": player.get("roleLabel") or info.get("position"),
        "player_status": player.get("playerStatus"),
        "date_of_birth": player.get("dateOfBirth"),
        "nationality": player.get("nationality"),
    }


def match_row(match: dict) -> dict:
    home = match.get("home") or {}
    away = match.get("away") or {}
    return {
        "match_id": match.get("matchId"),
        "date_local": match.get("matchDateLocal"),
        "date_utc": match.get("matchDateUtc"),
        "round": match.get("roundName"),
        "status": match.get("status"),
        "home_team_id": home.get("teamId"),
        "home_team": home.get("officialName") or home.get("shortName"),
        "home_score": match.get("providerHomeScore", match.get("homeScorePush")),
        "away_team_id": away.get("teamId"),
        "away_team": away.get("officialName") or away.get("shortName"),
        "away_score": match.get("providerAwayScore", match.get("awayScorePush")),
        "stadium": match.get("stadiumName"),
    }


def build_team_stat_rows(
    response: dict,
    match: dict,
    selected_team_id: str,
) -> list[dict]:
    home = match.get("home") or {}
    away = match.get("away") or {}
    selected_is_home = home.get("teamId") == selected_team_id
    selected = home if selected_is_home else away
    opponent = away if selected_is_home else home
    selected_value_key = "statsValueHome" if selected_is_home else "statsValueAway"
    opponent_value_key = "statsValueAway" if selected_is_home else "statsValueHome"

    rows = []
    for stat in response.get("stats", []):
        rows.append(
            {
                "match_id": match.get("matchId"),
                "date_local": match.get("matchDateLocal"),
                "round": match.get("roundName"),
                "team_id": selected.get("teamId"),
                "team": selected.get("officialName") or selected.get("shortName"),
                "opponent_id": opponent.get("teamId"),
                "opponent": opponent.get("officialName") or opponent.get("shortName"),
                "is_home": selected_is_home,
                "stat_id": stat.get("statsId"),
                "stat_label": stat.get("statsLabel"),
                "value": stat.get(selected_value_key),
                "opponent_value": stat.get(opponent_value_key),
            }
        )
    return rows


def player_match_rows(
    response: dict,
    match: dict,
    selected_team_id: str,
) -> list[dict]:
    rows = []
    for entry in response.get("players", []):
        player = entry.get("player") or {}
        team = entry.get("team") or {}
        if team.get("teamId") != selected_team_id:
            continue

        row = {
            "match_id": match.get("matchId"),
            "date_local": match.get("matchDateLocal"),
            "round": match.get("roundName"),
            "team_id": team.get("teamId"),
            "team": team.get("officialName") or team.get("shortName"),
            "player_id": player.get("playerId"),
            "provider_player_id": player.get("providerId"),
            "player": player.get("displayName"),
            "shirt_name": player.get("shirtName"),
            "shirt_number": player.get("bibNumber"),
            "position": player.get("roleLabel"),
        }
        row.update(
            {
                stat.get("statsId"): stat.get("statsValue")
                for stat in entry.get("stats", [])
                if stat.get("statsId")
            }
        )
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", required=True, help="Official team name, e.g. Milan")
    parser.add_argument("--season", default="2025/2026", help="Season name, e.g. 2025/2026")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between API requests")
    parser.add_argument("--output-root", default="data/raw/lega_serie_a")
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay must be zero or greater")

    client = LegaClient(args.delay)
    seasons_response = client.get(
        f"competitions/{quote(COMPETITION_ID, safe='')}/seasons"
    )
    season = next(
        (
            item
            for item in seasons_response.get("seasons", [])
            if item.get("seasonName", "").casefold() == args.season.casefold()
        ),
        None,
    )
    if season is None:
        available = [item.get("seasonName") for item in seasons_response.get("seasons", [])]
        raise RuntimeError(f"Season {args.season!r} not found. Available: {available}")

    season_id = season["seasonId"]
    season_path_id = quote(season_id, safe="")
    match_response = client.get(f"seasons/{season_path_id}/matches")
    roster_response = client.get(f"teams/{season_path_id}/rosters")
    rosters = roster_response.get("rosters", [])
    team_name = normalize_team_name(args.team)
    roster = next(
        (item for item in rosters if team_name in team_names(item.get("team") or {})),
        None,
    )
    if roster is None:
        available = sorted(
            {
                team.get("officialName") or team.get("shortName") or ""
                for item in rosters
                if (team := item.get("team") or {})
            }
        )
        raise RuntimeError(f"Team {args.team!r} not found. Available: {available}")

    team = roster.get("team") or {}
    team_id = team["teamId"]
    team_display_name = team.get("officialName") or team.get("shortName") or args.team
    fixtures = [
        match
        for match in match_response.get("matches", [])
        if team_id
        in {
            (match.get("home") or {}).get("teamId"),
            (match.get("away") or {}).get("teamId"),
        }
    ]
    finished_fixtures = [match for match in fixtures if match.get("status") == "FINISHED"]

    output_dir = (
        Path(args.output_root)
        / args.season.replace("/", "-")
        / safe_slug(team_display_name)
    )
    raw_dir = output_dir / "raw"
    write_json(raw_dir / "season_matches.json", match_response)
    write_json(raw_dir / "season_roster.json", roster)

    match_rows = [match_row(match) for match in fixtures]
    roster_rows = [roster_row(player) for player in roster.get("players", [])]
    team_stat_rows = []
    player_stat_rows = []
    failed_team_stats = []
    failed_player_stats = []

    with (raw_dir / "match_stats.jsonl").open("w", encoding="utf-8") as raw_output:
        for index, match in enumerate(finished_fixtures, start=1):
            match_id = match["matchId"]
            match_path_id = quote(match_id, safe="")
            raw_match = {"match": match}
            try:
                team_response = client.get(
                    f"seasons/{season_path_id}/match/{match_path_id}/teamstats"
                )
                raw_match["teamstats"] = team_response
                rows = build_team_stat_rows(team_response, match, team_id)
                if rows:
                    team_stat_rows.extend(rows)
                else:
                    failed_team_stats.append(
                        {"match_id": match_id, "error": "Empty team stats response"}
                    )
            except Exception as error:
                failed_team_stats.append({"match_id": match_id, "error": str(error)})

            try:
                player_response = client.get(
                    f"seasons/{season_path_id}/match/{match_path_id}/playerstats"
                )
                raw_match["playerstats"] = player_response
                rows = player_match_rows(player_response, match, team_id)
                if rows:
                    player_stat_rows.extend(rows)
                else:
                    failed_player_stats.append(
                        {"match_id": match_id, "error": "No player rows for selected team"}
                    )
            except Exception as error:
                failed_player_stats.append({"match_id": match_id, "error": str(error)})

            raw_output.write(json.dumps(raw_match, ensure_ascii=False) + "\n")
            print(f"[{index}/{len(finished_fixtures)}] {match_row(match)['home_team']} - "
                  f"{match_row(match)['away_team']}")

    write_csv(
        output_dir / "matches.csv",
        match_rows,
        [
            "match_id", "date_local", "date_utc", "round", "status",
            "home_team_id", "home_team", "home_score", "away_team_id",
            "away_team", "away_score", "stadium",
        ],
    )
    write_csv(
        output_dir / "team_match_stats.csv",
        team_stat_rows,
        [
            "match_id", "date_local", "round", "team_id", "team",
            "opponent_id", "opponent", "is_home", "stat_id", "stat_label",
            "value", "opponent_value",
        ],
    )
    write_csv(
        output_dir / "player_match_stats.csv",
        player_stat_rows,
        [
            "match_id", "date_local", "round", "team_id", "team", "player_id",
            "provider_player_id", "player", "shirt_name", "shirt_number", "position",
        ],
    )
    write_csv(
        output_dir / "season_roster.csv",
        roster_rows,
        [
            "player_id", "provider_player_id", "name", "shirt_name", "first_name",
            "last_name", "shirt_number", "position", "player_status",
            "date_of_birth", "nationality",
        ],
    )

    manifest = {
        "source": "Lega Serie A statistics API",
        "season": args.season,
        "season_id": season_id,
        "team": team_display_name,
        "team_id": team_id,
        "downloaded_utc": datetime.now(timezone.utc).isoformat(),
        "team_fixture_count": len(fixtures),
        "finished_fixture_count": len(finished_fixtures),
        "expected_finished_fixture_count": 38 if args.season == "2025/2026" else None,
        "fixture_count_complete": (
            len(finished_fixtures) == 38 if args.season == "2025/2026" else None
        ),
        "roster_player_count": len(roster_rows),
        "roster_unique_player_count": len(
            {row["player_id"] for row in roster_rows if row["player_id"]}
        ),
        "player_match_unique_player_count": len(
            {row["player_id"] for row in player_stat_rows if row["player_id"]}
        ),
        "roster_players_without_match_stats": sorted(
            row["name"]
            for row in roster_rows
            if row["player_id"]
            and row["player_id"]
            not in {match_row["player_id"] for match_row in player_stat_rows}
        ),
        "team_stats_match_count": len(finished_fixtures) - len(failed_team_stats),
        "player_stats_match_count": len(finished_fixtures) - len(failed_player_stats),
        "player_match_row_count": len(player_stat_rows),
        "team_stats_complete": not failed_team_stats,
        "player_stats_complete": not failed_player_stats,
        "failed_team_stats": failed_team_stats,
        "failed_player_stats": failed_player_stats,
    }
    write_json(output_dir / "manifest.json", manifest)
    print(f"\nSaved {manifest['team_fixture_count']} fixtures to {output_dir}")
    print(
        f"Roster: {manifest['roster_player_count']} players; "
        f"player-match rows: {manifest['player_match_row_count']}; "
        f"team-stat fixtures: {manifest['team_stats_match_count']}/"
        f"{manifest['finished_fixture_count']}"
    )
    if failed_team_stats or failed_player_stats or manifest["fixture_count_complete"] is False:
        print("Download is incomplete; see manifest.json and rerun to retry.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())