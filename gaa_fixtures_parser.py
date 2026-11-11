#!/usr/bin/env python3
"""
GAA fixtures feed client and parser.

Reads the fixtures and results listings that county board sites built on the
kilkennygaa.ie platform serve from /fixtures-results/ for a venue or a team.
"""

import argparse
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

DEFAULT_SITE_URL = "https://kilkennygaa.ie"
FEED_PATH = "/fixtures-results/"
PAGE_SIZE = 50
MAX_PAGES = 20
SCOPE_KINDS = ("venue", "team")

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_ISO_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2}) - (\d{4}-\d{2}-\d{2})$")
_ORDINAL = re.compile(r"(\d+)(st|nd|rd|th)\b")
_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_MAX_RANGE_DAYS = 31


class FeedError(Exception):
    """the site returned something other than a usable listing"""


@dataclass(frozen=True)
class Scope:
    kind: str
    id: str

    @property
    def param(self) -> str:
        return f"{self.kind}_id"

    def __str__(self) -> str:
        return f"{self.kind} {self.id}"


@dataclass(frozen=True)
class Fixture:
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
    raw_html: str


def parse_gaa_date(text: str) -> str:
    """'Sunday 15th Jun 2025' or an ISO date or range, to the ISO (first) date"""
    text = " ".join(text.split())
    match = _ISO_RANGE.match(text)
    if match:
        return date.fromisoformat(match.group(1)).isoformat()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    return datetime.strptime(_ORDINAL.sub(r"\1", text), "%A %d %b %Y").date().isoformat()


def expand_date_range(text: str) -> list[str]:
    """an ISO range header becomes one ISO date per day; anything else is returned as is"""
    match = _ISO_RANGE.match(" ".join(text.split()))
    if not match:
        return [text]
    start, end = date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))
    days = (end - start).days
    if not 0 <= days <= _MAX_RANGE_DAYS:
        raise ValueError(f"implausible date range {text!r}")
    return [(start + timedelta(days=n)).isoformat() for n in range(days + 1)]


def fixture_start(date_parsed: str, time: str, tz: tzinfo) -> tuple[datetime, bool]:
    """start in the site's timezone, and whether the time is unknown (all day)"""
    day = date.fromisoformat(date_parsed)
    match = _CLOCK.match(time.strip())
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz), False
    return datetime(day.year, day.month, day.day, tzinfo=tz), True


def is_walkover(time: str) -> bool:
    """a side conceded, so no match was played"""
    return time.strip().lower() == "conceded"


def _text(element: Tag) -> str:
    return " ".join(element.get_text(" ", strip=True).split())


def _uuid_in(href: str) -> str:
    found = UUID_RE.findall(urlparse(href).path)
    return found[-1].lower() if found else ""


def _is_listing_element(tag: Tag) -> bool:
    classes = tag.get("class") or []
    return (tag.name == "h3" and "fix_res_date" in classes) or (
        tag.name == "div" and "competition" in classes
    )


def _team(element: Tag) -> tuple[str, str]:
    link = element.find("a", href=re.compile(r"/fixtures-results/team/"))
    if link is None:
        return _text(element), ""
    return _text(link), _uuid_in(link["href"])


def _competition(element: Tag) -> tuple[str, str, str]:
    link = element.find("a", href=True)
    if link is None:
        return _text(element), "", ""
    parts = [p for p in urlparse(link["href"]).path.split("/") if p]
    sport = parts[1] if len(parts) > 2 and parts[0] == "fixtures-results" else ""
    return _text(link), _uuid_in(link["href"]), sport


def _more_info(element: Tag) -> tuple[str, str, str]:
    text = _text(element)
    referee = re.search(r"Referee:\s*(.*)$", text)
    link = element.find("a", href=re.compile(r"/fixtures-results/venue/"))
    if link is not None:
        venue, venue_id = _text(link), _uuid_in(link["href"])
    else:
        found = re.search(r"Venue:\s*(.*?)\s*(?:Referee:|$)", text)
        venue, venue_id = (found.group(1) if found else ""), ""
    return venue, venue_id, referee.group(1).strip() if referee else ""


def _parse_block(block: Tag, date_text: str, status: str) -> list[Fixture]:
    name = block.find("div", class_="competition-name")
    home = block.find("div", class_="home_team")
    away = block.find("div", class_="away_team")
    time = block.find("div", class_="time")
    info = block.find("div", class_="more_info")
    if any(part is None for part in (name, home, away, time, info)):
        raise FeedError(f"unrecognised fixture block under {date_text!r}")
    try:
        dates = expand_date_range(date_text)
        parsed = [parse_gaa_date(d) for d in dates]
    except ValueError as e:
        raise FeedError(f"unparseable date header {date_text!r}") from e

    competition, competition_id, sport = _competition(name)
    home_team, home_team_id = _team(home)
    away_team, away_team_id = _team(away)
    venue, venue_id, referee = _more_info(info)
    raw_html = str(block)
    return [
        Fixture(
            date=shown,
            date_parsed=iso,
            time=_text(time),
            sport=sport,
            competition=competition,
            competition_id=competition_id,
            home_team=home_team,
            home_team_id=home_team_id,
            away_team=away_team,
            away_team_id=away_team_id,
            venue=venue,
            venue_id=venue_id,
            referee=referee,
            status=status,
            raw_html=raw_html,
        )
        for shown, iso in zip(dates, parsed, strict=True)
    ]


def _summary(html: str) -> str:
    text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())
    return text[:200] or "<empty>"


def parse_listing(html: str, status: str) -> list[Fixture]:
    """
    parse one listing fragment. an empty listing must say so explicitly; anything
    else without fixture blocks (an outage notice, a redesign) is an error.
    """
    soup = BeautifulSoup(html, "html.parser")
    fixtures: list[Fixture] = []
    date_text = None
    blocks = 0
    for element in soup.find_all(_is_listing_element):
        if element.name == "h3":
            date_text = _text(element)
            continue
        blocks += 1
        if date_text is None:
            raise FeedError("fixture block appears before any date header")
        fixtures.extend(_parse_block(element, date_text, status))
    if blocks == 0 and soup.find(class_="no-data-message") is None:
        raise FeedError(f"unrecognised listing: {_summary(html)}")
    return fixtures


class FeedClient:
    """fetches the ajax listing the site's own 'load more' button uses"""

    def __init__(
        self,
        site_url: str = DEFAULT_SITE_URL,
        session: requests.Session | None = None,
        timeout: float = 30,
        page_size: int = PAGE_SIZE,
        max_pages: int = MAX_PAGES,
    ):
        self._url = site_url.rstrip("/") + FEED_PATH
        self._session = session or requests.Session()
        self._timeout = timeout
        self._page_size = page_size
        self._max_pages = max_pages

    def fetch(self, scope: Scope, feed_type: str, max_pages: int | None = None) -> list[Fixture]:
        """
        every page of a fixtures or results feed, or only the first max_pages.
        raises FeedError for anything that is not a well-formed listing.
        """
        status = {"fixtures": "fixture", "results": "result"}[feed_type]
        limit = max_pages or self._max_pages
        fixtures: list[Fixture] = []
        for page in range(limit):
            payload = self._get_page(scope, feed_type, page)
            if page == 0 or payload["html"].strip():
                fixtures.extend(parse_listing(payload["html"], status))
            if not payload["hasMore"]:
                return fixtures
        if max_pages is not None:
            return fixtures
        raise FeedError(f"{feed_type} for {scope} still reports more pages after {limit}")

    def _get_page(self, scope: Scope, feed_type: str, page: int) -> dict:
        where = f"{feed_type} page {page} for {scope}"
        params = {
            "ajax": "1",
            "feed_type": feed_type,
            "page": page,
            "size": self._page_size,
            scope.param: scope.id,
        }
        try:
            response = self._session.get(
                self._url,
                params=params,
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise FeedError(f"{where}: {e}") from e
        try:
            payload = response.json()
        except ValueError as e:
            raise FeedError(f"{where} was not JSON: {_summary(response.text)}") from e
        if not (
            isinstance(payload, dict)
            and payload.get("ok") is True
            and isinstance(payload.get("html"), str)
            and isinstance(payload.get("hasMore"), bool)
        ):
            raise FeedError(f"{where} was not a listing: {str(payload)[:200]}")
        return payload


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="print fixtures from the live feed")
    parser.add_argument("kind", choices=SCOPE_KINDS)
    parser.add_argument("id", help="uuid from the venue or team page URL")
    parser.add_argument("--results", action="store_true", help="latest results instead")
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    args = parser.parse_args(argv)

    client = FeedClient(args.site_url)
    feed_type = "results" if args.results else "fixtures"
    max_pages = 1 if args.results else None
    for f in client.fetch(Scope(args.kind, args.id), feed_type, max_pages=max_pages):
        print(f"{f.date_parsed} {f.time:>5}  {f.home_team} v {f.away_team}  ({f.venue})")


if __name__ == "__main__":
    main()
