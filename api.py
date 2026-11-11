#!/usr/bin/env python3
"""
GAA Fixtures REST API

Serves cached fixtures for the configured venues or teams as JSON, and as an
iCal feed for calendar clients such as Home Assistant's Remote Calendar.
"""

import asyncio
import copy
import hashlib
import logging
import os
import threading
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from icalendar import Calendar, Event
from pydantic import BaseModel
from uvicorn.config import LOGGING_CONFIG

from fixtures_store import FixtureStore, StoredFixture
from gaa_fixtures_parser import (
    DEFAULT_SITE_URL,
    UUID_RE,
    FeedClient,
    FeedError,
    Scope,
    fixture_start,
    is_walkover,
)

logger = logging.getLogger(__name__)

MATCH_LENGTH = timedelta(hours=2)
RETIRED_SETTINGS = ("CLUB_ID", "COUNTY_BOARD_ID")
SCOPE_SETTINGS = (("venue", "VENUE_IDS"), ("team", "TEAM_IDS"))


class ConfigError(Exception):
    """settings the service cannot run with"""


def _ids(env: Mapping[str, str], name: str) -> list[str]:
    ids = [part.strip().lower() for part in env.get(name, "").split(",") if part.strip()]
    for value in ids:
        if not UUID_RE.fullmatch(value):
            raise ConfigError(f"{name}: {value!r} is not a UUID from a venue or team page URL")
    return ids


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        raise ConfigError(f"{name} must be a positive whole number, not {raw!r}")
    return value


@dataclass(frozen=True)
class Settings:
    site_url: str
    scopes: tuple[Scope, ...]
    db_path: str
    fetch_interval_minutes: int
    stale_after_minutes: int
    timezone: ZoneInfo
    port: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        scopes = tuple(
            Scope(kind, value) for kind, name in SCOPE_SETTINGS for value in _ids(env, name)
        )
        retired = [name for name in RETIRED_SETTINGS if env.get(name)]
        if not scopes:
            hint = "set VENUE_IDS or TEAM_IDS to UUIDs from venue or team page URLs on the site"
            if retired:
                verb = "is" if len(retired) == 1 else "are"
                raise ConfigError(
                    f"{' and '.join(retired)} {verb} no longer supported, the site retired"
                    f" that feed: {hint}"
                )
            raise ConfigError(hint)
        if retired:
            logger.warning("ignoring retired settings %s", ", ".join(retired))

        interval = _positive_int(env, "FETCH_INTERVAL_MINUTES", 60)
        tz_name = env.get("SITE_TIMEZONE") or "Europe/Dublin"
        try:
            timezone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ConfigError(f"SITE_TIMEZONE {tz_name!r} is not a known timezone") from e
        return cls(
            site_url=env.get("SITE_URL") or DEFAULT_SITE_URL,
            scopes=scopes,
            db_path=env.get("DB_PATH") or "fixtures.db",
            fetch_interval_minutes=interval,
            stale_after_minutes=_positive_int(env, "STALE_AFTER_MINUTES", 3 * interval),
            timezone=timezone,
            port=_positive_int(env, "PORT", 8000),
        )


class ScopeModel(BaseModel):
    kind: str
    id: str


class FixtureResponse(BaseModel):
    id: int
    date: str
    date_parsed: str
    time: str
    sport: str
    competition: str
    home_team: str
    away_team: str
    venue: str
    referee: str
    status: str
    created_at: str
    last_seen_at: str


class FixturesListResponse(BaseModel):
    fixtures: list[FixtureResponse]
    total_count: int
    scopes: list[ScopeModel]


class HealthResponse(BaseModel):
    status: str
    scopes: list[ScopeModel]
    database_path: str
    last_update: str | None
    last_sync_attempt_at: str | None
    last_sync_success_at: str | None
    last_sync_error: str | None
    consecutive_failures: int
    total_fixtures: int
    upcoming_fixtures: int


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat() if moment else None


def _fixture_response(f: StoredFixture) -> FixtureResponse:
    return FixtureResponse(
        id=f.id,
        date=f.date,
        date_parsed=f.date_parsed,
        time=f.time,
        sport=f.sport,
        competition=f.competition,
        home_team=f.home_team,
        away_team=f.away_team,
        venue=f.venue,
        referee=f.referee,
        status=f.status,
        created_at=_iso(f.created_at),
        last_seen_at=_iso(f.last_seen_at),
    )


def calendar_ics(fixtures: list[StoredFixture], timezone: ZoneInfo, stamp: datetime) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//local-gaa-fixtures-api//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "GAA Fixtures")
    for f in fixtures:
        if is_walkover(f.time):
            continue
        start, all_day = fixture_start(f.date_parsed, f.time, timezone)
        event = Event()
        # derived from the match identity so a rebuilt database keeps the same uids
        event.add(
            "uid", f"{hashlib.sha256(f.key.encode()).hexdigest()[:32]}@local-gaa-fixtures-api"
        )
        event.add("dtstamp", stamp.astimezone(UTC))
        event.add("created", f.created_at.astimezone(UTC))
        event.add("summary", f"{f.home_team} v {f.away_team}")
        if all_day:
            event.add("dtstart", start.date())
            event.add("dtend", start.date() + timedelta(days=1))
        else:
            event.add("dtstart", start.astimezone(UTC))
            event.add("dtend", (start + MATCH_LENGTH).astimezone(UTC))
        if f.venue:
            event.add("location", f.venue)
        details = [f"Competition: {f.competition}", f"Venue: {f.venue}", f"Referee: {f.referee}"]
        if all_day and f.time:
            details.append(f"Time: {f.time}")
        event.add("description", "\n".join(details))
        event.add("categories", ["GAA", f.sport.title()] if f.sport else ["GAA"])
        cal.add_component(event)
    return cal.to_ical()


class Syncer:
    """one sync at a time; every outcome lands in the store's sync state"""

    def __init__(
        self,
        client: FeedClient,
        store: FixtureStore,
        scopes: tuple[Scope, ...],
        now: Callable[[], datetime],
    ):
        self._client = client
        self._store = store
        self._scopes = scopes
        self._now = now
        self._lock = threading.Lock()

    def run(self) -> None:
        if not self._lock.acquire(blocking=False):
            logger.info("sync already in progress")
            return
        try:
            self._sync()
        finally:
            self._lock.release()

    def _sync(self) -> None:
        started = self._now()
        try:
            upcoming = [self._client.fetch(s, "fixtures") for s in self._scopes]
            played = [self._client.fetch(s, "results", max_pages=1) for s in self._scopes]
            for scope, fixtures, results in zip(self._scopes, upcoming, played, strict=True):
                # the site answers an unknown id with its normal empty listing
                if not fixtures and not results:
                    raise FeedError(f"{scope} lists no fixtures or results; check the id")
            summary = self._store.apply_sync(upcoming + played, started)
        except Exception as e:
            expected = isinstance(e, FeedError)
            message = str(e) if expected else f"{type(e).__name__}: {e}"
            logger.error("sync failed: %s", message, exc_info=not expected)
            try:
                self._store.record_failure(started, message)
            except Exception:
                logger.exception("could not record the sync failure")
            return
        logger.info("sync ok: %d listed, %d no longer listed", summary.listed, summary.removed)


async def _every(seconds: float, job: Callable[[], None]) -> None:
    while True:
        await asyncio.sleep(seconds)
        await asyncio.to_thread(job)


def create_app(
    settings: Settings | None = None,
    client: FeedClient | None = None,
    now: Callable[[], datetime] | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    now = now or (lambda: datetime.now(UTC))
    store = FixtureStore(settings.db_path, settings.timezone)
    syncer = Syncer(client or FeedClient(settings.site_url), store, settings.scopes, now)
    scopes = [ScopeModel(kind=s.kind, id=s.id) for s in settings.scopes]

    def today() -> date:
        return now().astimezone(settings.timezone).date()

    def listing(fixtures: list[StoredFixture], total: int) -> FixturesListResponse:
        return FixturesListResponse(
            fixtures=[_fixture_response(f) for f in fixtures], total_count=total, scopes=scopes
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await asyncio.to_thread(syncer.run)
        task = asyncio.create_task(_every(settings.fetch_interval_minutes * 60, syncer.run))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="GAA Fixtures API",
        description="REST API and iCal feed for GAA fixtures",
        version="2.0.3",
        lifespan=lifespan,
    )

    @app.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
    def health() -> JSONResponse:
        state = store.sync_state()
        total, upcoming = store.counts(today())
        fresh = state.last_success_at is not None and now() - state.last_success_at <= timedelta(
            minutes=settings.stale_after_minutes
        )
        body = HealthResponse(
            status="healthy" if fresh else "unhealthy",
            scopes=scopes,
            database_path=settings.db_path,
            last_update=_iso(state.last_success_at),
            last_sync_attempt_at=_iso(state.last_attempt_at),
            last_sync_success_at=_iso(state.last_success_at),
            last_sync_error=state.last_error,
            consecutive_failures=state.consecutive_failures,
            total_fixtures=total,
            upcoming_fixtures=upcoming,
        )
        return JSONResponse(body.model_dump(), status_code=200 if fresh else 503)

    @app.get("/fixtures", response_model=FixturesListResponse)
    def get_fixtures(
        limit: int = 50, offset: int = 0, include_past: bool = False, venue: str | None = None
    ):
        fixtures, total = store.list_fixtures(
            today=today(), include_past=include_past, venue=venue, limit=limit, offset=offset
        )
        return listing(fixtures, total)

    # played fixtures stay in the feed so calendars keep their history
    @app.get("/fixtures/calendar.ics")
    def get_fixtures_calendar(include_past: bool = True, venue: str | None = None) -> Response:
        fixtures, _ = store.list_fixtures(
            today=today(), include_past=include_past, venue=venue, limit=None
        )
        return Response(
            content=calendar_ics(fixtures, settings.timezone, now()),
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": "inline; filename=gaa-fixtures.ics"},
        )

    @app.get("/fixtures/venues")
    def get_venues():
        return {"venues": store.venues()}

    @app.get("/fixtures/competitions")
    def get_competitions():
        return {"competitions": store.competitions()}

    @app.get("/fixtures/by-venue/{venue}", response_model=FixturesListResponse)
    def get_fixtures_by_venue(
        venue: str, limit: int = 50, offset: int = 0, include_past: bool = False
    ):
        fixtures, total = store.list_fixtures(
            today=today(), include_past=include_past, venue=venue, limit=limit, offset=offset
        )
        if total == 0:
            raise HTTPException(status_code=404, detail="No fixtures found for this venue")
        return listing(fixtures, total)

    @app.get("/fixtures/by-competition/{competition}", response_model=FixturesListResponse)
    def get_fixtures_by_competition(
        competition: str, limit: int = 50, offset: int = 0, include_past: bool = False
    ):
        fixtures, total = store.list_fixtures(
            today=today(),
            include_past=include_past,
            competition=competition,
            limit=limit,
            offset=offset,
        )
        if total == 0:
            raise HTTPException(
                status_code=404, detail="No upcoming fixtures found for this competition"
            )
        return listing(fixtures, total)

    @app.get("/fixtures/{fixture_id}", response_model=FixtureResponse)
    def get_fixture(fixture_id: int):
        fixture = store.get_fixture(fixture_id)
        if fixture is None:
            raise HTTPException(status_code=404, detail="Fixture not found")
        return _fixture_response(fixture)

    @app.post("/fixtures/refresh")
    def refresh_fixtures(background_tasks: BackgroundTasks):
        background_tasks.add_task(syncer.run)
        return {"message": "Fixtures refresh triggered"}

    return app


class QuietHealthChecks(logging.Filter):
    """the container health check polls every 30s; keep it out of the access log"""

    def filter(self, record: logging.LogRecord) -> bool:
        return '"GET /health ' not in record.getMessage()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        raise SystemExit(f"configuration error: {e}") from e
    log_config = copy.deepcopy(LOGGING_CONFIG)
    log_config.setdefault("filters", {})["quiet_health"] = {"()": QuietHealthChecks}
    log_config["handlers"]["access"]["filters"] = ["quiet_health"]
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port, log_config=log_config)


if __name__ == "__main__":
    main()
