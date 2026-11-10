from contextlib import contextmanager
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from api import ConfigError, Settings, create_app
from gaa_fixtures_parser import FeedClient, Scope
from tests.support import (
    HOME_ID,
    OTHER_VENUE_ID,
    VENUE_ID,
    Clock,
    FakeSite,
    block,
    listing,
    read_data,
)

DUBLIN = ZoneInfo("Europe/Dublin")
T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def settings_for(tmp_path, name="fixtures.db"):
    return Settings(
        site_url="https://example.ie",
        scopes=(Scope("venue", VENUE_ID),),
        db_path=str(tmp_path / name),
        fetch_interval_minutes=60,
        stale_after_minutes=180,
        timezone=DUBLIN,
        port=8000,
    )


@contextmanager
def running(tmp_path, site, clock, db="fixtures.db"):
    client = FeedClient("https://example.ie", session=site)
    app = create_app(settings_for(tmp_path, db), client=client, now=clock)
    with TestClient(app) as http:
        yield http


def calendar_lines(http, **params) -> list[str]:
    response = http.get("/fixtures/calendar.ics", params=params)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    return response.text.splitlines()


def one_fixture_site(date_header: str, time: str = "14:00", **block_args) -> FakeSite:
    return FakeSite(fixtures=[listing((date_header, [block(time=time, **block_args)]))])


def test_health_is_unhealthy_while_the_site_serves_its_outage_notice(tmp_path):
    site = FakeSite()
    site.outage = True

    with running(tmp_path, site, Clock(T0)) as http:
        response = http.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert "temporarily unavailable" in body["last_sync_error"]
    assert body["last_sync_success_at"] is None


def test_health_is_healthy_after_a_successful_sync(tmp_path):
    site = FakeSite(fixtures=[read_data("fixtures_listing.html")])

    with running(tmp_path, site, Clock(T0)) as http:
        response = http.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["total_fixtures"], body["upcoming_fixtures"]) == ("healthy", 3, 3)


def test_recent_success_keeps_health_green_through_a_failed_sync(tmp_path):
    site = FakeSite(fixtures=[read_data("fixtures_listing.html")])
    clock = Clock(T0)

    with running(tmp_path, site, clock) as http:
        site.outage = True
        clock.advance(minutes=60)
        http.post("/fixtures/refresh")
        response = http.get("/health")

    assert response.status_code == 200
    assert response.json()["consecutive_failures"] == 1


def test_health_goes_unhealthy_when_nothing_succeeds_within_the_stale_window(tmp_path):
    site = FakeSite(fixtures=[read_data("fixtures_listing.html")])
    clock = Clock(T0)

    with running(tmp_path, site, clock) as http:
        clock.advance(minutes=181)
        response = http.get("/health")

    assert response.status_code == 503
    assert response.json()["last_sync_success_at"] == "2026-11-10T12:00:00+00:00"


def test_refresh_endpoint_recovers_after_an_outage(tmp_path):
    site = FakeSite(fixtures=[read_data("fixtures_listing.html")])
    site.outage = True

    with running(tmp_path, site, Clock(T0)) as http:
        assert http.get("/health").status_code == 503
        site.outage = False
        http.post("/fixtures/refresh")
        response = http.get("/health")

    assert response.status_code == 200


def test_unexpected_sync_error_is_reported_and_later_syncs_still_run(tmp_path):
    class Flaky:
        def __init__(self, site):
            self.site = site
            self.broken = True

        def get(self, *args, **kwargs):
            if self.broken:
                raise RuntimeError("parser bug")
            return self.site.get(*args, **kwargs)

    session = Flaky(FakeSite(fixtures=[read_data("fixtures_listing.html")]))

    with running(tmp_path, session, Clock(T0)) as http:
        failed = http.get("/health")
        session.broken = False
        http.post("/fixtures/refresh")
        recovered = http.get("/health")

    assert failed.status_code == 503
    assert failed.json()["last_sync_error"] == "RuntimeError: parser bug"
    assert recovered.status_code == 200


def test_scope_with_no_fixtures_or_results_is_flagged(tmp_path, caplog):
    with running(tmp_path, FakeSite(), Clock(T0)):
        pass

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(VENUE_ID in message for message in warnings)


def test_scope_with_only_results_is_not_flagged(tmp_path, caplog):
    site = FakeSite(results=[listing(("Monday 9th Nov 2026", [block()]))])

    with running(tmp_path, site, Clock(T0)):
        pass

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


@pytest.mark.parametrize(
    ("date_header", "time", "now", "dtstart", "dtend"),
    [
        (
            "Saturday 14th Nov 2026",
            "14:00",
            T0,
            "DTSTART:20261114T140000Z",
            "DTEND:20261114T160000Z",
        ),
        (
            "Wednesday 10th Jun 2026",
            "19:00",
            datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
            "DTSTART:20260610T180000Z",
            "DTEND:20260610T200000Z",
        ),
    ],
)
def test_calendar_times_are_utc_instants_of_irish_local_times(
    tmp_path, date_header, time, now, dtstart, dtend
):
    with running(tmp_path, one_fixture_site(date_header, time), Clock(now)) as http:
        lines = calendar_lines(http)

    assert dtstart in lines
    assert dtend in lines


def test_calendar_fixture_without_a_time_is_an_all_day_event(tmp_path):
    with running(tmp_path, one_fixture_site("Saturday 21st Nov 2026", "TBC"), Clock(T0)) as http:
        lines = calendar_lines(http)

    assert "DTSTART;VALUE=DATE:20261121" in lines
    assert "DTEND;VALUE=DATE:20261122" in lines


def test_calendar_uids_survive_a_database_rebuild(tmp_path):
    def uids(http):
        lines = calendar_lines(http, include_past="false")
        return [line for line in lines if line.startswith("UID:")]

    site = FakeSite(fixtures=[read_data("fixtures_listing.html")])
    with running(tmp_path, site, Clock(T0), db="first.db") as http:
        first = uids(http)

    # the rebuilt database saw an older fixture first, so its row ids differ
    site.pages["fixtures"] = [listing(("Monday 9th Nov 2026", [block(home=("Clara", None))]))]
    with running(tmp_path, site, Clock(T0), db="second.db") as http:
        site.pages["fixtures"] = [read_data("fixtures_listing.html")]
        http.post("/fixtures/refresh")
        second = uids(http)

    assert first == second
    assert len(set(first)) == 3


def test_calendar_drops_a_fixture_the_site_no_longer_lists(tmp_path):
    site = one_fixture_site("Sunday 15th Nov 2026")
    clock = Clock(T0)

    with running(tmp_path, site, clock) as http:
        assert "BEGIN:VEVENT" in calendar_lines(http)
        site.pages["fixtures"] = [read_data("no_fixtures.html")]
        for _ in range(2):
            clock.advance(minutes=60)
            http.post("/fixtures/refresh")
        lines = calendar_lines(http)

    assert "BEGIN:VEVENT" not in lines


def test_calendar_keeps_played_fixtures_unless_asked_for_upcoming_only(tmp_path):
    site = FakeSite(
        fixtures=[
            listing(
                ("Monday 9th Nov 2026", [block(home=("Clara", None))]),
                ("Sunday 15th Nov 2026", [block(home=("Piltown", None))]),
            )
        ]
    )

    with running(tmp_path, site, Clock(T0)) as http:
        everything = calendar_lines(http)
        upcoming = calendar_lines(http, include_past="false")

    assert "SUMMARY:Clara v James Stephens" in everything
    assert "SUMMARY:Piltown v James Stephens" in everything
    assert "SUMMARY:Clara v James Stephens" not in upcoming
    assert "SUMMARY:Piltown v James Stephens" in upcoming


def test_calendar_venue_filter_matches_part_of_the_name(tmp_path):
    site = FakeSite(
        fixtures=[
            listing(
                (
                    "Sunday 15th Nov 2026",
                    [
                        block(home=("Clara", None), venue=("UPMC Nowlan Park", VENUE_ID)),
                        block(home=("Piltown", None), venue=("Tom Ryall Park", OTHER_VENUE_ID)),
                    ],
                )
            )
        ]
    )

    with running(tmp_path, site, Clock(T0)) as http:
        lines = calendar_lines(http, venue="Nowlan")

    assert "LOCATION:UPMC Nowlan Park" in lines
    assert "LOCATION:Tom Ryall Park" not in lines


def test_fixtures_default_to_today_onwards(tmp_path):
    site = FakeSite(
        fixtures=[
            listing(
                ("Monday 9th Nov 2026", [block(home=("Clara", None))]),
                ("Sunday 15th Nov 2026", [block(home=("Piltown", None))]),
            )
        ]
    )

    with running(tmp_path, site, Clock(T0)) as http:
        upcoming = http.get("/fixtures").json()
        everything = http.get("/fixtures", params={"include_past": "true"}).json()

    assert [f["home_team"] for f in upcoming["fixtures"]] == ["Piltown"]
    assert everything["total_count"] == 2


def test_settings_require_a_venue_or_team():
    with pytest.raises(ConfigError, match="VENUE_IDS"):
        Settings.from_env({})


def test_retired_club_settings_are_explained():
    with pytest.raises(ConfigError, match="CLUB_ID"):
        Settings.from_env({"CLUB_ID": "1234", "COUNTY_BOARD_ID": "15"})


def test_settings_accept_several_venues_and_teams():
    settings = Settings.from_env(
        {"VENUE_IDS": f"{VENUE_ID}, {OTHER_VENUE_ID}", "TEAM_IDS": HOME_ID.upper()}
    )

    assert settings.scopes == (
        Scope("venue", VENUE_ID),
        Scope("venue", OTHER_VENUE_ID),
        Scope("team", HOME_ID),
    )


def test_settings_reject_an_id_that_is_not_a_uuid():
    with pytest.raises(ConfigError, match="upmc-nowlan-park"):
        Settings.from_env({"VENUE_IDS": "upmc-nowlan-park"})


def test_stale_window_defaults_to_three_fetch_intervals():
    settings = Settings.from_env({"VENUE_IDS": VENUE_ID, "FETCH_INTERVAL_MINUTES": "20"})

    assert settings.stale_after_minutes == 60
