"""
SQLite cache of fixtures, kept in step with the site's listings.

Rows are keyed on what identifies a match (date, competition, teams), so a new
time, venue or referee updates the row in place. A future fixture that drops out
of the listings is hidden only after missing two successful syncs, and never
within a day of its start, when the site may drop it before throw-in.
"""

import logging
import re
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

from gaa_fixtures_parser import Fixture, fixture_start

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
REMOVAL_GRACE = timedelta(hours=24)
MISSES_BEFORE_REMOVAL = 2

_SCHEMA = """
CREATE TABLE fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_key TEXT NOT NULL UNIQUE,
    date TEXT NOT NULL,
    date_parsed TEXT NOT NULL,
    time TEXT NOT NULL,
    start_at TEXT NOT NULL,
    sport TEXT NOT NULL,
    competition TEXT NOT NULL,
    competition_id TEXT NOT NULL,
    home_team TEXT NOT NULL,
    home_team_id TEXT NOT NULL,
    away_team TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    venue_id TEXT NOT NULL,
    referee TEXT NOT NULL,
    status TEXT NOT NULL,
    raw_html TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    missed_syncs INTEGER NOT NULL DEFAULT 0,
    removed_at TEXT
);
CREATE INDEX fixtures_start ON fixtures (start_at);
CREATE TABLE sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);
INSERT INTO sync_state (id) VALUES (1);
"""

_UPSERT = """
INSERT INTO fixtures (
    fixture_key, date, date_parsed, time, start_at, sport, competition, competition_id,
    home_team, home_team_id, away_team, away_team_id, venue, venue_id, referee, status,
    raw_html, created_at, last_seen_at, missed_syncs, removed_at
) VALUES (
    :fixture_key, :date, :date_parsed, :time, :start_at, :sport, :competition,
    :competition_id, :home_team, :home_team_id, :away_team, :away_team_id, :venue,
    :venue_id, :referee, :status, :raw_html, :now, :now, 0, NULL
)
ON CONFLICT (fixture_key) DO UPDATE SET
    date = excluded.date,
    date_parsed = excluded.date_parsed,
    time = excluded.time,
    start_at = excluded.start_at,
    sport = excluded.sport,
    competition = excluded.competition,
    competition_id = excluded.competition_id,
    home_team = excluded.home_team,
    home_team_id = excluded.home_team_id,
    away_team = excluded.away_team,
    away_team_id = excluded.away_team_id,
    venue = excluded.venue,
    venue_id = excluded.venue_id,
    referee = excluded.referee,
    status = excluded.status,
    raw_html = excluded.raw_html,
    last_seen_at = excluded.last_seen_at,
    missed_syncs = 0,
    removed_at = NULL
"""

_CONCEDED = re.compile(r"\s*\(c\)$")


@dataclass(frozen=True)
class StoredFixture:
    id: int
    key: str
    date: str
    date_parsed: str
    time: str
    sport: str
    competition: str
    competition_id: str
    home_team: str
    home_team_id: str
    away_team: str
    away_team_id: str
    venue: str
    venue_id: str
    referee: str
    status: str
    created_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class SyncState:
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int


@dataclass(frozen=True)
class SyncSummary:
    listed: int
    removed: int


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _team_ref(name: str, team_id: str) -> str:
    # a walkover marks the conceding side "(C)" once the result is in
    return f"{team_id}:{_CONCEDED.sub('', _norm(name))}"


def fixture_key(f: Fixture) -> str:
    return "|".join(
        (
            f.date_parsed,
            f.competition_id or _norm(f.competition),
            _team_ref(f.home_team, f.home_team_id),
            _team_ref(f.away_team, f.away_team_id),
        )
    )


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _moment(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _stored(row: sqlite3.Row) -> StoredFixture:
    return StoredFixture(
        id=row["id"],
        key=row["fixture_key"],
        date=row["date"],
        date_parsed=row["date_parsed"],
        time=row["time"],
        sport=row["sport"],
        competition=row["competition"],
        competition_id=row["competition_id"],
        home_team=row["home_team"],
        home_team_id=row["home_team_id"],
        away_team=row["away_team"],
        away_team_id=row["away_team_id"],
        venue=row["venue"],
        venue_id=row["venue_id"],
        referee=row["referee"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
    )


class FixtureStore:
    def __init__(self, db_path: str, timezone: tzinfo):
        self.db_path = db_path
        self.timezone = timezone
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate(self) -> None:
        with closing(self._connect()) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                return
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"{self.db_path} has schema {version}, newer than this release"
                    f" supports ({SCHEMA_VERSION})"
                )
            script = "BEGIN;\n"
            legacy = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'fixtures'"
            ).fetchone()
            if legacy:
                logger.warning("keeping the pre-2.0 fixtures table as fixtures_legacy")
                script += "ALTER TABLE fixtures RENAME TO fixtures_legacy;\n"
            script += _SCHEMA + f"PRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;\n"
            conn.executescript(script)

    def apply_sync(self, listings: Sequence[Sequence[Fixture]], now: datetime) -> SyncSummary:
        """record one complete, successful read of every listing"""
        merged: dict[str, Fixture] = {}
        for listing in listings:
            seen: dict[str, list[Fixture]] = {}
            for fixture in listing:
                base = fixture_key(fixture)
                same_teams = seen.setdefault(base, [])
                if fixture in same_teams:
                    # listed twice, e.g. when the pages shifted while being read
                    continue
                same_teams.append(fixture)
                merged[base if len(same_teams) == 1 else f"{base}#{len(same_teams)}"] = fixture

        stamp = _iso(now)
        with closing(self._connect()) as conn, conn:
            for key, f in merged.items():
                start, _ = fixture_start(f.date_parsed, f.time, self.timezone)
                conn.execute(
                    _UPSERT,
                    {
                        "fixture_key": key,
                        "date": f.date,
                        "date_parsed": f.date_parsed,
                        "time": f.time,
                        "start_at": _iso(start),
                        "sport": f.sport,
                        "competition": f.competition,
                        "competition_id": f.competition_id,
                        "home_team": f.home_team,
                        "home_team_id": f.home_team_id,
                        "away_team": f.away_team,
                        "away_team_id": f.away_team_id,
                        "venue": f.venue,
                        "venue_id": f.venue_id,
                        "referee": f.referee,
                        "status": f.status,
                        "raw_html": f.raw_html,
                        "now": stamp,
                    },
                )
            removed = self._age_unlisted(conn, set(merged), now)
            conn.execute(
                "UPDATE sync_state SET last_attempt_at = ?, last_success_at = ?,"
                " last_error = NULL, consecutive_failures = 0 WHERE id = 1",
                (stamp, stamp),
            )
        return SyncSummary(listed=len(merged), removed=removed)

    def _age_unlisted(self, conn: sqlite3.Connection, listed: set[str], now: datetime) -> int:
        horizon = (now.astimezone(self.timezone).date() - timedelta(days=1)).isoformat()
        rows = conn.execute(
            "SELECT id, fixture_key, date_parsed, time, missed_syncs FROM fixtures"
            " WHERE removed_at IS NULL AND date_parsed >= ?",
            (horizon,),
        ).fetchall()
        removed = 0
        for row in rows:
            if row["fixture_key"] in listed:
                continue
            start, _ = fixture_start(row["date_parsed"], row["time"], self.timezone)
            if start - now <= REMOVAL_GRACE:
                continue
            missed = row["missed_syncs"] + 1
            removed_at = _iso(now) if missed >= MISSES_BEFORE_REMOVAL else None
            removed += removed_at is not None
            conn.execute(
                "UPDATE fixtures SET missed_syncs = ?, removed_at = ? WHERE id = ?",
                (missed, removed_at, row["id"]),
            )
        return removed

    def record_failure(self, now: datetime, error: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE sync_state SET last_attempt_at = ?, last_error = ?,"
                " consecutive_failures = consecutive_failures + 1 WHERE id = 1",
                (_iso(now), error),
            )

    def sync_state(self) -> SyncState:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM sync_state WHERE id = 1").fetchone()
        return SyncState(
            last_attempt_at=_moment(row["last_attempt_at"]),
            last_success_at=_moment(row["last_success_at"]),
            last_error=row["last_error"],
            consecutive_failures=row["consecutive_failures"],
        )

    def list_fixtures(
        self,
        *,
        today: date,
        include_past: bool = False,
        venue: str | None = None,
        competition: str | None = None,
        limit: int | None = 50,
        offset: int = 0,
    ) -> tuple[list[StoredFixture], int]:
        """visible fixtures in start order; limit None returns them all"""
        where = ["removed_at IS NULL"]
        params: list[object] = []
        if not include_past:
            where.append("date_parsed >= ?")
            params.append(today.isoformat())
        if venue:
            where.append("venue LIKE ?")
            params.append(f"%{venue}%")
        if competition is not None:
            where.append("competition = ?")
            params.append(competition)
        clause = " AND ".join(where)
        with closing(self._connect()) as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM fixtures WHERE {clause}", params).fetchone()
            rows = conn.execute(
                f"SELECT * FROM fixtures WHERE {clause} ORDER BY start_at, id LIMIT ? OFFSET ?",
                [*params, -1 if limit is None else limit, offset],
            ).fetchall()
        return [_stored(r) for r in rows], total[0]

    def get_fixture(self, fixture_id: int) -> StoredFixture | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM fixtures WHERE id = ? AND removed_at IS NULL", (fixture_id,)
            ).fetchone()
        return _stored(row) if row else None

    def _distinct(self, column: str) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT DISTINCT {column} FROM fixtures"
                f" WHERE {column} != '' AND removed_at IS NULL ORDER BY {column}"
            ).fetchall()
        return [r[0] for r in rows]

    def venues(self) -> list[str]:
        return self._distinct("venue")

    def competitions(self) -> list[str]:
        return self._distinct("competition")

    def counts(self, today: date) -> tuple[int, int]:
        """visible fixtures in total, and from today on"""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*), COUNT(CASE WHEN date_parsed >= ? THEN 1 END) FROM fixtures"
                " WHERE removed_at IS NULL",
                (today.isoformat(),),
            ).fetchone()
        return row[0], row[1]
