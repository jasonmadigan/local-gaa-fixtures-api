import pytest

from gaa_fixtures_parser import FeedError, parse_gaa_date, parse_listing
from tests.support import read_data


def test_parses_every_field_of_a_fixture_block():
    first = parse_listing(read_data("fixtures_listing.html"), "fixture")[0]

    assert first.date == "Saturday 14th Nov 2026"
    assert first.date_parsed == "2026-11-14"
    assert first.time == "14:00"
    assert first.sport == "hurling"
    assert first.competition == "St. Canice's Credit Union Senior Hurling League - Round 3"
    assert first.competition_id == "aaaaaaaa-0000-4000-8000-000000000001"
    assert first.home_team == "Dicksboro"
    assert first.home_team_id == "bbbbbbbb-0000-4000-8000-000000000001"
    assert first.away_team == "James Stephens"
    assert first.away_team_id == "bbbbbbbb-0000-4000-8000-000000000002"
    assert first.venue == "UPMC Nowlan Park"
    assert first.venue_id == "cccccccc-0000-4000-8000-000000000001"
    assert first.referee == "TBC"
    assert first.status == "fixture"


def test_blocks_take_the_date_of_the_preceding_header():
    fixtures = parse_listing(read_data("fixtures_listing.html"), "fixture")

    assert [(f.date_parsed, f.home_team) for f in fixtures] == [
        ("2026-11-14", "Dicksboro"),
        ("2026-11-14", "O'Loughlin Gaels"),
        ("2026-11-22", "Dicksboro B"),
    ]


def test_venue_containing_capital_r_is_kept():
    second = parse_listing(read_data("fixtures_listing.html"), "fixture")[1]

    assert second.venue == "Tom Ryall Park"
    assert second.referee == "Pat Byrne"


def test_venue_without_link_is_read_from_text():
    third = parse_listing(read_data("fixtures_listing.html"), "fixture")[2]

    assert third.venue == "Rathdowney GAA Grounds"
    assert third.venue_id == ""
    assert third.referee == ""


def test_team_without_link_has_name_but_no_id():
    third = parse_listing(read_data("fixtures_listing.html"), "fixture")[2]

    assert third.away_team == "TBC"
    assert third.away_team_id == ""


def test_unconfirmed_time_is_kept_verbatim():
    second = parse_listing(read_data("fixtures_listing.html"), "fixture")[1]

    assert second.time == "TBC"
    assert second.sport == "football"


def test_iso_date_range_becomes_one_fixture_per_day():
    html = read_data("fixtures_listing.html").replace(
        "Saturday 14th Nov 2026", "2026-08-01 - 2026-08-03", 1
    )

    fixtures = parse_listing(html, "fixture")

    assert [(f.date_parsed, f.home_team) for f in fixtures[:6]] == [
        ("2026-08-01", "Dicksboro"),
        ("2026-08-02", "Dicksboro"),
        ("2026-08-03", "Dicksboro"),
        ("2026-08-01", "O'Loughlin Gaels"),
        ("2026-08-02", "O'Loughlin Gaels"),
        ("2026-08-03", "O'Loughlin Gaels"),
    ]


def test_explicit_empty_listing_has_no_fixtures():
    assert parse_listing(read_data("no_fixtures.html"), "fixture") == []


def test_unavailable_notice_is_an_error_not_an_empty_listing():
    with pytest.raises(FeedError, match="temporarily unavailable"):
        parse_listing(read_data("unavailable.html"), "fixture")


def test_unrecognised_markup_is_an_error():
    with pytest.raises(FeedError):
        parse_listing("<div class='new-layout'><span>Sat 14 Nov</span></div>", "fixture")


def test_block_without_team_elements_is_an_error():
    html = (
        '<h3 class="fix_res_date">Saturday 14th Nov 2026</h3>'
        '<div class="competition"><div class="competition-name">Senior League</div></div>'
    )

    with pytest.raises(FeedError, match="Saturday 14th Nov 2026"):
        parse_listing(html, "fixture")


@pytest.mark.parametrize("slot", ["time", "more_info"])
def test_block_missing_its_time_or_venue_line_is_an_error(slot):
    html = read_data("fixtures_listing.html").replace(
        f'<div class="{slot}', '<div class="renamed', 1
    )

    with pytest.raises(FeedError, match="Saturday 14th Nov 2026"):
        parse_listing(html, "fixture")


def test_unparseable_date_header_is_an_error():
    html = read_data("fixtures_listing.html").replace("Saturday 14th Nov 2026", "Saturday TBC", 1)

    with pytest.raises(FeedError, match="Saturday TBC"):
        parse_listing(html, "fixture")


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("Sunday 1st Nov 2026", "2026-11-01"),
        ("Monday 22nd Jun 2026", "2026-06-22"),
        ("Wednesday 3rd Jun 2026", "2026-06-03"),
        ("Thursday 11th Jun 2026", "2026-06-11"),
        ("2026-08-02", "2026-08-02"),
        ("2026-08-01 - 2026-08-03", "2026-08-01"),
    ],
)
def test_parse_gaa_date(text, iso):
    assert parse_gaa_date(text) == iso
