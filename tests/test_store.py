import sqlite3
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures_store import FixtureStore
from tests.support import make_fixture

DUBLIN = ZoneInfo("Europe/Dublin")
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
TODAY = date(2026, 11, 10)


@pytest.fixture
def store(tmp_path):
    return FixtureStore(str(tmp_path / "fixtures.db"), DUBLIN)


def listed(store, **kwargs):
    fixtures, _ = store.list_fixtures(today=TODAY, **kwargs)
    return fixtures


def hourly_syncs(store, listing, count, start=NOW):
    for n in range(1, count + 1):
        store.apply_sync([listing], start + timedelta(hours=n))


def test_synced_fixture_is_listed(store):
    store.apply_sync([[make_fixture()]], NOW)

    fixtures, total = store.list_fixtures(today=TODAY)

    assert total == 1
    assert (fixtures[0].home_team, fixtures[0].time, fixtures[0].created_at) == (
        "Dicksboro",
        "14:00",
        NOW,
    )


def test_changed_time_updates_the_fixture_in_place(store):
    store.apply_sync([[make_fixture(time="14:00")]], NOW)
    first_id = listed(store)[0].id

    store.apply_sync([[make_fixture(time="15:30")]], NOW + timedelta(hours=1))

    assert [(f.id, f.time) for f in listed(store)] == [(first_id, "15:30")]


def test_future_fixture_missing_from_one_sync_is_kept(store):
    store.apply_sync([[make_fixture()]], NOW)

    hourly_syncs(store, [], 1)

    assert len(listed(store)) == 1


def test_future_fixture_missing_from_two_syncs_is_hidden(store):
    store.apply_sync([[make_fixture()]], NOW)

    hourly_syncs(store, [], 2)

    assert listed(store) == []
    assert listed(store, include_past=True) == []


def test_hidden_fixture_returns_when_listed_again(store):
    store.apply_sync([[make_fixture()]], NOW)
    hourly_syncs(store, [], 2)

    store.apply_sync([[make_fixture()]], NOW + timedelta(hours=3))

    assert len(listed(store)) == 1


def test_fixture_within_a_day_of_start_is_kept_when_missing(store):
    soon = make_fixture(date="Tuesday 10th Nov 2026", date_parsed="2026-11-10", time="15:00")
    store.apply_sync([[soon]], NOW)

    hourly_syncs(store, [], 3)

    assert [f.time for f in listed(store)] == ["15:00"]


def test_past_fixture_is_kept_when_missing(store):
    played = make_fixture(date="Monday 9th Nov 2026", date_parsed="2026-11-09")
    store.apply_sync([[played]], NOW)

    hourly_syncs(store, [], 3)

    assert [f.date_parsed for f in listed(store, include_past=True)] == ["2026-11-09"]


def test_same_fixture_from_several_scopes_is_stored_once(store):
    store.apply_sync([[make_fixture()], [make_fixture()]], NOW)

    assert store.list_fixtures(today=TODAY)[1] == 1


def test_two_meetings_of_the_same_teams_on_one_day_are_both_kept(store):
    morning = make_fixture(home_team="TBC", home_team_id="", away_team="TBC", away_team_id="")
    evening = make_fixture(
        home_team="TBC", home_team_id="", away_team="TBC", away_team_id="", time="18:00"
    )

    store.apply_sync([[morning, evening]], NOW)

    assert [f.time for f in listed(store)] == ["14:00", "18:00"]


def test_result_replaces_fixture_status(store):
    store.apply_sync([[make_fixture(status="fixture")]], NOW)

    store.apply_sync([[], [make_fixture(status="result")]], NOW + timedelta(hours=1))

    assert [f.status for f in listed(store)] == ["result"]


def test_conceded_marker_does_not_duplicate_a_fixture(store):
    store.apply_sync([[make_fixture(away_team="Clara")]], NOW)

    conceded = make_fixture(away_team="Clara (C)", status="result")
    store.apply_sync([[conceded]], NOW + timedelta(hours=1))

    assert [f.away_team for f in listed(store, include_past=True)] == ["Clara (C)"]


def test_failure_is_recorded_without_touching_fixtures(store):
    store.apply_sync([[make_fixture()]], NOW)

    store.record_failure(NOW + timedelta(hours=1), "fixtures page 0: 503 Server Error")

    state = store.sync_state()
    assert state.last_success_at == NOW
    assert state.last_attempt_at == NOW + timedelta(hours=1)
    assert state.last_error == "fixtures page 0: 503 Server Error"
    assert state.consecutive_failures == 1
    assert len(listed(store)) == 1


def test_consecutive_failures_accumulate_and_success_resets_them(store):
    store.record_failure(NOW, "first")
    store.record_failure(NOW + timedelta(hours=1), "second")
    assert (store.sync_state().consecutive_failures, store.sync_state().last_error) == (2, "second")

    store.apply_sync([[make_fixture()]], NOW + timedelta(hours=2))

    state = store.sync_state()
    assert (state.consecutive_failures, state.last_error) == (0, None)


def test_sync_state_survives_reopening(tmp_path):
    path = str(tmp_path / "fixtures.db")
    FixtureStore(path, DUBLIN).apply_sync([[make_fixture()]], NOW)

    assert FixtureStore(path, DUBLIN).sync_state().last_success_at == NOW


def test_upcoming_fixtures_are_chronological_and_exclude_past(store):
    store.apply_sync(
        [
            [
                make_fixture(date_parsed="2026-11-09", home_team="Clara"),
                make_fixture(date_parsed="2026-11-15", time="15:00", home_team="Erins Own"),
                make_fixture(date_parsed="2026-11-15", time="9:30", home_team="Mullinavat"),
                make_fixture(date_parsed="2026-11-12", time="19:00", home_team="Piltown"),
            ]
        ],
        NOW,
    )

    fixtures, total = store.list_fixtures(today=TODAY)

    assert total == 3
    assert [f.home_team for f in fixtures] == ["Piltown", "Mullinavat", "Erins Own"]


def test_venue_filter_is_a_partial_case_insensitive_match(store):
    store.apply_sync(
        [
            [
                make_fixture(venue="UPMC Nowlan Park", home_team="Clara"),
                make_fixture(venue="Tom Ryall Park", home_team="Piltown"),
            ]
        ],
        NOW,
    )

    assert [f.home_team for f in listed(store, venue="nowlan")] == ["Clara"]


def test_legacy_table_is_kept_aside(tmp_path):
    path = tmp_path / "fixtures.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE fixtures (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,"
            " date_parsed TEXT NOT NULL, competition TEXT NOT NULL, home_team TEXT NOT NULL,"
            " away_team TEXT NOT NULL, time TEXT NOT NULL, venue TEXT NOT NULL,"
            " referee TEXT NOT NULL, raw_html TEXT NOT NULL, created_at TEXT NOT NULL,"
            " UNIQUE(date, competition, home_team, away_team, time))"
        )
        conn.execute(
            "INSERT INTO fixtures (date, date_parsed, competition, home_team, away_team, time,"
            " venue, referee, raw_html, created_at) VALUES ('Sunday 15th Jun 2025',"
            " '2025-06-15', 'League', 'Clara', 'Piltown', '11:00', 'Clara', 'TBC', '', '')"
        )
    conn.close()

    store = FixtureStore(str(path), DUBLIN)
    store.apply_sync([[make_fixture()]], NOW)

    assert len(listed(store)) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT home_team FROM fixtures_legacy").fetchall() == [("Clara",)]
    conn.close()


def test_database_from_a_newer_release_is_refused(tmp_path):
    path = tmp_path / "fixtures.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 99")
    conn.close()

    with pytest.raises(RuntimeError, match="newer"):
        FixtureStore(str(path), DUBLIN)
