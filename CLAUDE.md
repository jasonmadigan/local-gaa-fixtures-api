# GAA Fixtures API - Claude Context

## Project Overview
A REST API service that fetches and serves GAA fixtures data from Kilkenny GAA. The service is configurable per club and containerised for easy deployment.

## Key Architecture Decisions

### Parser (`gaa_fixtures_parser.py`)
- Fetches HTML from GAA website: `https://kilkennygaa.ie/fixtures-results/fixtures-results-ajax/?clubID={club_id}&countyBoardID={county_board_id}&fixturesOnly=Y`
- Parses HTML using BeautifulSoup to extract fixture data from structured divs
- Stores data in SQLite with proper date parsing for chronological sorting
- **Date parsing**: Converts "Sunday 15th Jun 2025" → "2025-06-15" for proper sorting
- **Problem solved**: GAA website drops games shortly before they start, so caching is critical

### API (`api.py`)
- FastAPI-based REST service
- **Default behavior**: Only returns today and future fixtures (excludes past games)
- Configurable via environment variables for different clubs
- Background task refreshes fixtures every hour

### Database Schema
```sql
CREATE TABLE fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,                    -- Original "Sunday 15th Jun 2025"
    date_parsed TEXT NOT NULL,             -- Parsed "2025-06-15" for sorting
    competition TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    time TEXT NOT NULL,
    venue TEXT NOT NULL,
    referee TEXT NOT NULL,
    raw_html TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(date, competition, home_team, away_team, time)
);
```

## API Endpoints

### Core Endpoints
- `GET /health` - Service health and stats
- `GET /fixtures` - All fixtures (today and future by default)
- `GET /fixtures/{id}` - Specific fixture by ID
- `POST /fixtures/refresh` - Manual refresh trigger

### Filtering Endpoints
- `GET /fixtures/competitions` - List available competitions
- `GET /fixtures/by-competition/{competition}` - Fixtures by competition
- `GET /fixtures/venues` - List available venues  
- `GET /fixtures/by-venue/{venue}` - Fixtures by venue

### Query Parameters
- `limit` (default: 50) - Pagination
- `offset` (default: 0) - Pagination
- `include_past` (default: false) - Include past fixtures
- `venue` - Filter by venue name (partial match)

## Configuration (Environment Variables)
- `CLUB_ID` (default: 2107) - GAA Club ID
- `COUNTY_BOARD_ID` (default: 15) - County Board ID
- `DB_PATH` (default: fixtures.db) - SQLite database path
- `FETCH_INTERVAL_MINUTES` (default: 60) - Refresh frequency
- `PORT` (default: 8000) - API server port

## Development Setup

### With uv (Recommended)
```bash
uv sync                           # Install dependencies
uv run python api.py             # Start API server
uv run python gaa_fixtures_parser.py  # Test parser directly
```

### Testing Examples
```bash
# Get upcoming fixtures (default)
curl "http://localhost:8000/fixtures"

# Get home fixtures at Tullogher
curl "http://localhost:8000/fixtures?venue=Tullogher"

# Get all venues
curl "http://localhost:8000/fixtures/venues"

# Include past fixtures (if needed)
curl "http://localhost:8000/fixtures?include_past=true"
```

## Docker Support
- Multi-stage build with uv for fast dependency installation
- Health checks configured
- Persistent data via volume mounts

## Key Implementation Details

### HTML Parsing Strategy
The GAA website returns structured HTML with:
- Date headers: `<h3 class="fix_res_date">`
- Competition blocks: `<div class="competition">`
- Team names in: `<div class="home_team">` and `<div class="away_team">`
- Time in: `<div class="time">`
- Venue/referee in: `<div class="more_info">`

### Filtering Logic
- **Default behavior**: `date_parsed >= today` (excludes past games)
- **Venue filtering**: `venue LIKE %{venue}%` (partial matching)
- **Chronological sorting**: `ORDER BY date_parsed ASC, time ASC`

### Background Processing
- Automatic refresh every hour via asyncio background task
- Manual refresh endpoint for immediate updates
- Graceful error handling for network issues

## Common Issues & Solutions

1. **Parsing failures**: Check if GAA website HTML structure changed
2. **Missing team names**: Verify CSS selectors in `_parse_competition_block`
3. **Wrong club data**: Verify CLUB_ID and COUNTY_BOARD_ID are correct
4. **Database schema changes**: Remove old `fixtures.db` and restart

## Testing Commands
```bash
# Lint and format
uv run black .
uv run ruff check .

# Database inspection
sqlite3 fixtures.db "SELECT date, home_team, away_team, venue FROM fixtures LIMIT 5;"

# Parser testing with different club
CLUB_ID=1234 COUNTY_BOARD_ID=20 uv run python gaa_fixtures_parser.py
```

## Future Enhancements
- Add automated tests
- Support for results parsing (not just fixtures)
- Multi-club support in single instance
- Web interface for fixture display
- Push notifications for fixture updates