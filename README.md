# GAA Fixtures API

Caches GAA fixtures for chosen venues or teams from a county board website (kilkennygaa.ie by default) and serves them as a REST API and an iCal feed, for example to Home Assistant's Remote Calendar.

The site drops games from its fixtures list around throw-in, so the service keeps its own copy in SQLite and refreshes it hourly.

## Configuration

| Variable | Default | Description |
|-|-|-|
| `VENUE_IDS` | | Comma-separated venue UUIDs |
| `TEAM_IDS` | | Comma-separated team (club) UUIDs |
| `SITE_URL` | `https://kilkennygaa.ie` | County board site |
| `SITE_TIMEZONE` | `Europe/Dublin` | Timezone of the times the site shows |
| `DB_PATH` | `fixtures.db` | SQLite database; `/app/data/fixtures.db` in the image |
| `FETCH_INTERVAL_MINUTES` | `60` | How often to read the site |
| `STALE_AFTER_MINUTES` | 3 × fetch interval | `/health` fails once no sync has succeeded for this long |
| `PORT` | `8000` | Listen port |

Set at least one of `VENUE_IDS` or `TEAM_IDS`. The UUID is the last part of a venue or team page address under Fix & Res on the site, for example `https://kilkennygaa.ie/fixtures-results/venue/upmc-nowlan-park/2e69ec4d-97e3-4b7f-b4a5-e0b60666ad66/` or `https://kilkennygaa.ie/fixtures-results/team/dicksboro/9aa42b31-882e-c039-965f-3c4923ce1421/`. Some venues have more than one page; list every UUID. A team UUID belongs to the club, so it covers the club's B teams too.

| Setting | UUID | Covers |
|-|-|-|
| `VENUE_IDS` | `2e69ec4d-97e3-4b7f-b4a5-e0b60666ad66` | UPMC Nowlan Park |
| `VENUE_IDS` | `5f960223-d8d6-43e6-be8f-50e77522e689` | UPMC Nowlan Park, second page |
| `TEAM_IDS` | `9aa42b31-882e-c039-965f-3c4923ce1421` | Dicksboro, including its B teams |

A venue or team that lists no fixtures and no results fails the sync and shows in `/health`, because that is how the site answers a UUID it does not know.

### Upgrading from 1.x

`CLUB_ID` and `COUNTY_BOARD_ID` no longer work because the site retired that feed, and 2.0 refuses to start with only those set. An existing 1.x database is kept as table `fixtures_legacy` beside the new one.

## Running

```bash
uv sync
VENUE_IDS=2e69ec4d-97e3-4b7f-b4a5-e0b60666ad66 uv run python api.py
```

Docker. Mount `/app/data` so the cache survives container updates:

```bash
docker run -d --name gaa-fixtures -p 8000:8000 \
  -e VENUE_IDS=2e69ec4d-97e3-4b7f-b4a5-e0b60666ad66,5f960223-d8d6-43e6-be8f-50e77522e689 \
  -v "$(pwd)/data:/app/data" \
  ghcr.io/jasonmadigan/local-gaa-fixtures-api:latest
```

Docker Compose:

```yaml
services:
  gaa-fixtures-api:
    image: ghcr.io/jasonmadigan/local-gaa-fixtures-api:latest
    ports:
      - "8000:8000"
    environment:
      VENUE_IDS: 2e69ec4d-97e3-4b7f-b4a5-e0b60666ad66
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

Run one process per database: each process runs its own sync loop.

## API

- `GET /health`: sync status. 503 when the last successful sync is older than `STALE_AFTER_MINUTES`.
- `GET /fixtures`: fixtures from today on. `limit` (50), `offset` (0), `include_past` (false), `venue` (partial match).
- `GET /fixtures/{id}`
- `GET /fixtures/venues`, `GET /fixtures/competitions`
- `GET /fixtures/by-venue/{venue}`, `GET /fixtures/by-competition/{competition}`: same paging and `include_past`.
- `GET /fixtures/calendar.ics`: iCal feed including played fixtures. `include_past=false` limits it to today on; `venue` filters.
- `POST /fixtures/refresh`: sync now.

Interactive docs are at `/docs`.

A future fixture that disappears from the site (postponed or moved) is hidden after two successful syncs without it, but not within a day of its start. A failed sync leaves the cache as it was and shows in `/health`.

## Calendar feed

Each fixture is a two-hour event with UTC times, and played fixtures stay in the feed so calendars keep their history. A fixture without a confirmed time is an all-day event. Walkovers are left out, since no match is played; the JSON endpoints still list them, with time `Conceded`. Event UIDs depend only on the fixture, so a rebuilt database does not duplicate events in calendar clients.

## Home Assistant

Add the Remote Calendar integration with `http://<host>:8000/fixtures/calendar.ics`, optionally with `?venue=<name>`.

Remote Calendar only re-reads the feed once a day. To pick up short-notice fixtures and time changes sooner, refresh the entity from an automation:

```yaml
- alias: GAA calendar refresh
  triggers:
    - trigger: time_pattern
      minutes: /15
  actions:
    - action: homeassistant.update_entity
      target:
        entity_id: calendar.gaa
```

A calendar trigger, such as a reminder before each fixture, arms only if the calendar entity exists when Home Assistant starts its automations, and is not retried. If the API is unreachable then, Remote Calendar adds the entity later and the trigger stays unarmed until the next restart. Re-arm it when the calendar comes back:

```yaml
- alias: GAA reminder re-arm
  triggers:
    - trigger: state
      entity_id: calendar.gaa
      from: unavailable
  conditions:
    - condition: state
      entity_id: automation.gaa_fixture_reminder
      state: "on"
  actions:
    - action: automation.turn_off
      target:
        entity_id: automation.gaa_fixture_reminder
      data:
        stop_actions: false
    - action: automation.turn_on
      target:
        entity_id: automation.gaa_fixture_reminder
```

An all-day event starts at midnight, so check `trigger.calendar_event.all_day` before a reminder shows a time.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run black --check .
uv run python gaa_fixtures_parser.py venue 2e69ec4d-97e3-4b7f-b4a5-e0b60666ad66
uv run python gaa_fixtures_parser.py team 9aa42b31-882e-c039-965f-3c4923ce1421 --results
```

The last two commands print the live listing for a venue or a team, which helps when checking an id or when the site's markup changes.

## Releases

Pushes to `main` publish `ghcr.io/jasonmadigan/local-gaa-fixtures-api:latest`, and a `vX.Y.Z` tag publishes that tag. CI runs the linters, the tests and a Docker build first.

## License

MIT License - see LICENSE file for details.
