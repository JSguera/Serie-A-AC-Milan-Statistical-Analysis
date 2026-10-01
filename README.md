# Serie-A-AC-Milan-Statistical-Analysis
General Serie A/AC Milan EDA and eventual Hypothesis and testing

## Download Lega Serie A match data

The downloader uses the Lega Serie A statistics API to collect the selected
team's 2025/26 fixtures, per-match player statistics, and per-match team
statistics. It also saves the season roster separately so players without a
match appearance are not lost. Requests are made sequentially with a delay.

Run from the project root:

```powershell
python scripts/download_lega_serie_a.py --team "AC Milan"
```

Choose another team or season with `--team` and `--season`, for example:

```powershell
python scripts/download_lega_serie_a.py --team Inter --season 2024/2025
```

Files are written beneath `data/raw/lega_serie_a/<season>/<team>/`:

- `matches.csv`: one row per team fixture, including opponent and score.
- `team_match_stats.csv`: one row per team statistic per fixture, with the
	opponent's value alongside it.
- `player_match_stats.csv`: one row per selected-team player per fixture;
	statistic IDs become columns.
- `season_roster.csv`: the season roster returned by the league API.
- `manifest.json`: download counts and any missing match-stat responses.
- `raw/`: season match and roster responses plus per-match API responses.

The API is not documented as a stable public developer API, so the endpoint
format may change. Use this collector only within the permission you received
and the limits it specifies.
