# GAA Fixtures API - Claude Context

Caches GAA fixtures for configured venues or teams from a county board site (kilkennygaa.ie by default) and serves them as JSON and as an iCal feed for Home Assistant's Remote Calendar. Configuration and endpoints are in `README.md`.

## Layout

- `gaa_fixtures_parser.py`: feed client and listing parser. `uv run python gaa_fixtures_parser.py venue <uuid>` prints the live listing.
- `fixtures_store.py`: SQLite schema, migrations and sync bookkeeping.
- `api.py`: settings, sync loop, routes and iCal output.
- `tests/`: pytest. `tests/data/` holds listing markup copied from the site.

## The site's feed

- `GET {SITE_URL}/fixtures-results/?ajax=1&feed_type=fixtures|results&page=N&size=50&venue_id=<uuid>` (or `team_id`), the call behind the site's "Load more" button. It returns `{ok, html, hasMore, page}`.
- Pages count from 0. Only the id parameter filters; the page path and slug do not matter.
- `html` uses the long-standing markup: `h3.fix_res_date` headers, each followed by `div.competition` blocks holding `competition-name`, `home_team`, `time`, `away_team` and `more_info`.
- An empty listing says so with `div.no-data-message`. Anything else without blocks, such as the "Fixtures are temporarily unavailable" notice or a redesign, raises `FeedError`. That rule is what makes an outage visible in `/health`; keep it.
- A venue can have several UUIDs (UPMC Nowlan Park has two). Team UUIDs are per club, so a B team shares its club's UUID.
- Walkover results mark the conceding team `(C)` and show `Conceded` instead of a time.
- The 1.x endpoint `fixtures-results-ajax/?clubID=&countyBoardID=` was retired in 2026 and now always serves the outage notice.

## Store rules

- A fixture's identity is its date, competition UUID and both teams (UUID plus name, with `(C)` stripped). A new time, venue or referee updates the row in place.
- Each sync reads every fixtures page and the first results page, so a played fixture keeps its row.
- A future fixture missing from the listings is hidden after two successful syncs without it, never within 24 hours of its start. Past rows stay.
- A failed sync changes no fixtures; it is recorded in `sync_state`.
- A scope that lists no fixtures and no results fails the sync. The site answers an unknown UUID with its normal empty listing, so this is the only sign of a wrong or re-keyed id.
- `/health` returns 503 unless a sync succeeded within `STALE_AFTER_MINUTES`. The Docker health check depends on that status code.

## iCal

The feed keeps played fixtures (`include_past` defaults to true here, unlike the JSON endpoints). Times are UTC instants of the site's local times. An unknown time gives an all-day event. Walkovers (time `Conceded`) are left out because no match is played; the JSON endpoints keep them. UIDs come from the fixture identity, so a rebuilt database keeps them.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run black .
```

Python is pinned to 3.11 in `.python-version` to match the image; pydantic 2.5 has no wheels for 3.13. `httpx` stays below 0.28 while starlette is 0.27, whose TestClient breaks on 0.28.

Use neutral examples (UPMC Nowlan Park, Dicksboro) in code, docs, tests and commit messages. Real deployment IDs belong only in deployment configuration.

## Future Enhancements
- Web interface for fixture display
- Push notifications for fixture updates
