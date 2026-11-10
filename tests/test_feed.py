import json

import pytest
import requests

from gaa_fixtures_parser import FeedClient, FeedError, Scope
from tests.support import VENUE_ID, block, listing, read_data, response

SITE = "https://example.ie"
TEAM_ID = "bbbbbbbb-0000-4000-8000-000000000007"


class Session:
    """replays canned responses in order and records each request"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):  # noqa: ARG002
        self.calls.append((url, {k: str(v) for k, v in params.items()}))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def page(html: str, has_more: bool = False, number: int = 0) -> requests.Response:
    body = json.dumps({"ok": True, "html": html, "hasMore": has_more, "page": number})
    return response(200, body, "application/json", SITE)


def one_fixture(home: str) -> str:
    return listing(("Saturday 14th Nov 2026", [block(home=(home, None))]))


def test_requests_the_feed_for_the_configured_venue():
    session = Session(page(read_data("no_fixtures.html")))

    FeedClient(SITE, session=session).fetch(Scope("venue", VENUE_ID), "fixtures")

    assert session.calls == [
        (
            "https://example.ie/fixtures-results/",
            {
                "ajax": "1",
                "feed_type": "fixtures",
                "page": "0",
                "size": "50",
                "venue_id": VENUE_ID,
            },
        )
    ]


def test_team_scope_filters_by_team_id():
    session = Session(page(read_data("no_fixtures.html")))

    FeedClient(SITE, session=session).fetch(Scope("team", TEAM_ID), "fixtures")

    params = session.calls[0][1]
    assert params["team_id"] == TEAM_ID
    assert "venue_id" not in params


def test_follows_has_more_across_pages():
    session = Session(page(one_fixture("Dicksboro"), True, 0), page(one_fixture("Clara"), False, 1))

    fixtures = FeedClient(SITE, session=session).fetch(Scope("venue", VENUE_ID), "fixtures")

    assert [f.home_team for f in fixtures] == ["Dicksboro", "Clara"]
    assert [params["page"] for _, params in session.calls] == ["0", "1"]


def test_empty_page_after_the_first_ends_the_listing():
    session = Session(page(one_fixture("Dicksboro"), True, 0), page("", False, 1))

    fixtures = FeedClient(SITE, session=session).fetch(Scope("venue", VENUE_ID), "fixtures")

    assert [f.home_team for f in fixtures] == ["Dicksboro"]


def test_page_limit_stops_early_and_marks_results():
    session = Session(page(one_fixture("Dicksboro"), True, 0))

    fixtures = FeedClient(SITE, session=session).fetch(
        Scope("venue", VENUE_ID), "results", max_pages=1
    )

    assert [(f.home_team, f.status) for f in fixtures] == [("Dicksboro", "result")]
    assert len(session.calls) == 1


def test_pagination_that_never_ends_is_an_error():
    session = Session(*(page(one_fixture("Dicksboro"), True, n) for n in range(3)))

    with pytest.raises(FeedError, match="more pages"):
        FeedClient(SITE, session=session, max_pages=3).fetch(Scope("venue", VENUE_ID), "fixtures")


def test_html_outage_page_is_an_error():
    session = Session(response(200, read_data("unavailable.html"), "text/html", SITE))

    with pytest.raises(FeedError, match="temporarily unavailable"):
        FeedClient(SITE, session=session).fetch(Scope("venue", VENUE_ID), "fixtures")


def test_payload_not_marked_ok_is_an_error():
    body = json.dumps({"ok": False, "html": "", "hasMore": False, "page": 0})
    session = Session(response(200, body, "application/json", SITE))

    with pytest.raises(FeedError, match="not a listing"):
        FeedClient(SITE, session=session).fetch(Scope("venue", VENUE_ID), "fixtures")


def test_http_error_status_is_an_error():
    session = Session(response(404, "Not Found", "text/html", SITE))

    with pytest.raises(FeedError, match="404"):
        FeedClient(SITE, session=session).fetch(Scope("venue", VENUE_ID), "fixtures")


def test_connection_failure_is_an_error():
    session = Session(requests.ConnectionError("connection refused"))

    with pytest.raises(FeedError, match="connection refused"):
        FeedClient(SITE, session=session).fetch(Scope("venue", VENUE_ID), "fixtures")
