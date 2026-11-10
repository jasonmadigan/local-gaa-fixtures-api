import json
from datetime import timedelta
from pathlib import Path

import requests

from gaa_fixtures_parser import Fixture

DATA = Path(__file__).parent / "data"

VENUE_ID = "cccccccc-0000-4000-8000-000000000001"
OTHER_VENUE_ID = "cccccccc-0000-4000-8000-000000000009"
COMPETITION_ID = "aaaaaaaa-0000-4000-8000-000000000001"
HOME_ID = "bbbbbbbb-0000-4000-8000-000000000001"
AWAY_ID = "bbbbbbbb-0000-4000-8000-000000000002"

NO_RESULTS = """
            <div class="no-data-message text-center py-4">
                <p class="mb-0">No results found.</p>
            </div>
"""


def read_data(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


def make_fixture(**overrides) -> Fixture:
    values = {
        "date": "Sunday 15th Nov 2026",
        "date_parsed": "2026-11-15",
        "time": "14:00",
        "sport": "hurling",
        "competition": "Senior Hurling League - Round 1",
        "competition_id": COMPETITION_ID,
        "home_team": "Dicksboro",
        "home_team_id": HOME_ID,
        "away_team": "James Stephens",
        "away_team_id": AWAY_ID,
        "venue": "UPMC Nowlan Park",
        "venue_id": VENUE_ID,
        "referee": "TBC",
        "status": "fixture",
        "raw_html": '<div class="competition"></div>',
    }
    values.update(overrides)
    return Fixture(**values)


def _team(team: tuple[str, str | None]) -> str:
    name, team_id = team
    if team_id is None:
        return name
    return (
        f'<a class="ajax-listing-link" data-parmaters="&clubID={name}&leagueTable=y" '
        f'href="/fixtures-results/team/x/{team_id}/">{name}</a>'
    )


def block(
    *,
    competition: str = "Senior Hurling League - Round 1",
    sport: str = "hurling",
    competition_id: str = COMPETITION_ID,
    home: tuple[str, str | None] = ("Dicksboro", HOME_ID),
    away: tuple[str, str | None] = ("James Stephens", AWAY_ID),
    time: str = "14:00",
    venue: tuple[str, str] = ("UPMC Nowlan Park", VENUE_ID),
    referee: str = "TBC",
) -> str:
    """one fixture in the site's listing markup"""
    venue_name, venue_id = venue
    return f"""
    <div class="competition">
      <div class="d-flex"><div class="competition-name flex-fill text-center p-2">
        <a href="/fixtures-results/{sport}/club/senior/x/{competition_id}/">{competition}</a>
      </div></div>
      <div class="d-flex py-2 px-0 p-md-2 px-lg-0 no-gutters comp_details">
        <div class="home_team col text-center text-md-right align-self-center">{_team(home)}</div>
        <div class="home_score rounded {sport}">0-0</div>
        <div class="time rounded mx-2 {sport} ">{time}</div>
        <div class="away_score rounded {sport}">0-0</div>
        <div class="away_team col text-center text-md-left align-self-center">{_team(away)}</div>
      </div>
      <div class="more_info text-center pb-2"><p>
        <strong>Venue:</strong>
        <a class="" href="/fixtures-results/venue/x/{venue_id}/">{venue_name}</a>
        <span class="ml-2 d-block d-md-inline-block"> <strong>Referee:</strong> {referee}</span>
      </p></div>
    </div>"""


def listing(*days: tuple[str, list[str]]) -> str:
    return "".join(
        f'<h3 class="fix_res_date py-2 text-center mb-0">{date}</h3>' + "".join(blocks)
        for date, blocks in days
    )


def response(status: int, body: str, content_type: str, url: str) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body.encode("utf-8")
    r.headers["Content-Type"] = content_type
    r.encoding = "utf-8"
    r.url = url
    return r


class FakeSite:
    """stands in for requests.Session against the site's ajax feed"""

    def __init__(self, fixtures: list[str] | None = None, results: list[str] | None = None):
        self.pages = {
            "fixtures": fixtures or [read_data("no_fixtures.html")],
            "results": results or [NO_RESULTS],
        }
        self.outage = False
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url, params=None, headers=None, timeout=None):  # noqa: ARG002
        params = {k: str(v) for k, v in (params or {}).items()}
        self.calls.append((url, params))
        if self.outage:
            return response(200, read_data("unavailable.html"), "text/html; charset=UTF-8", url)
        pages = self.pages[params["feed_type"]]
        page = int(params["page"])
        html = pages[page] if page < len(pages) else ""
        payload = {"ok": True, "html": html, "hasMore": page + 1 < len(pages), "page": page}
        return response(200, json.dumps(payload), "application/json", url)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)
