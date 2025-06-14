# GAA Fixtures API

A REST API service that fetches and serves GAA fixtures data from Kilkenny GAA website. The service is configurable per club and can be containerised for easy deployment.

## Features

- 🏆 Fetches fixtures data from GAA website
- 📊 SQLite database for caching and persistence
- 🚀 FastAPI-based REST API
- 🐳 Docker support
- ⚙️ Configurable per club via environment variables
- 🔄 Automatic background refresh of fixtures data
- 📈 Health checks and monitoring endpoints

## API Endpoints

- `GET /health` - Health check with service status
- `GET /fixtures` - Get all fixtures (with pagination and filtering)
- `GET /fixtures/{id}` - Get specific fixture by ID
- `GET /fixtures/competitions` - Get list of available competitions
- `GET /fixtures/by-competition/{competition}` - Get fixtures for specific competition
- `GET /fixtures/venues` - Get list of available venues
- `GET /fixtures/by-venue/{venue}` - Get fixtures for specific venue
- `GET /fixtures/calendar.ics` - iCal calendar feed for CalDAV integration
- `POST /fixtures/refresh` - Manually trigger fixtures refresh

### Query Parameters

The `/fixtures` endpoint supports these optional parameters:
- `limit` (default: 50) - Number of fixtures to return
- `offset` (default: 0) - Number of fixtures to skip (for pagination)
- `include_past` (default: false) - Include past fixtures (by default only shows today and future)
- `venue` - Filter by venue name (partial match)

**Note:** By default, all endpoints only return fixtures for today and future dates. This is because the GAA website drops games shortly before they start, so our cache preserves this important data.

## Configuration

The application is configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CLUB_ID` | `2107` | GAA Club ID |
| `COUNTY_BOARD_ID` | `15` | County Board ID |
| `DB_PATH` | `fixtures.db` | SQLite database file path |
| `FETCH_INTERVAL_MINUTES` | `60` | How often to refresh fixtures (minutes) |
| `PORT` | `8000` | API server port |

## Quick Start

### Local Development with uv (Recommended)

1. **Install dependencies:**
   ```bash
   uv sync
   ```

2. **Set environment variables (optional):**
   ```bash
   export CLUB_ID=2107
   export COUNTY_BOARD_ID=15
   ```

3. **Run the API server:**
   ```bash
   uv run python api.py
   ```

4. **Access the API:**
   - API: http://localhost:8000
   - Interactive docs: http://localhost:8000/docs
   - Health check: http://localhost:8000/health

### Local Development with pip

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set environment variables (optional):**
   ```bash
   export CLUB_ID=2107
   export COUNTY_BOARD_ID=15
   ```

3. **Run the API server:**
   ```bash
   python api.py
   ```

### Docker

1. **Build the image:**
   ```bash
   docker build -t gaa-fixtures-api .
   ```

2. **Run the container:**
   ```bash
   docker run -d \
     --name gaa-fixtures \
     -p 8000:8000 \
     -e CLUB_ID=2107 \
     -e COUNTY_BOARD_ID=15 \
     -v $(pwd)/data:/app/data \
     gaa-fixtures-api
   ```

3. **Access the API:**
   - API: http://localhost:8000
   - Health check: http://localhost:8000/health

### Docker Compose

Create a `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  gaa-fixtures-api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - CLUB_ID=2107
      - COUNTY_BOARD_ID=15
      - FETCH_INTERVAL_MINUTES=60
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

Run with:
```bash
docker-compose up -d
```

## API Usage Examples

### Get all upcoming fixtures (default behavior)
```bash
curl "http://localhost:8000/fixtures?limit=10"
```

### Include past fixtures (if needed)
```bash
curl "http://localhost:8000/fixtures?include_past=true&limit=10"
```

### Get fixtures for a specific competition
```bash
curl "http://localhost:8000/fixtures/by-competition/Minor%20Hurling%20League"
```

### Get fixtures at a specific venue
```bash
curl "http://localhost:8000/fixtures/by-venue/Tullogher"
```

### Filter fixtures by venue (using query parameter)
```bash
curl "http://localhost:8000/fixtures?venue=Tullogher&limit=5"
```

### Get all available venues
```bash
curl "http://localhost:8000/fixtures/venues"
```

### Get service health
```bash
curl "http://localhost:8000/health"
```

### Get iCal calendar feed
```bash
curl "http://localhost:8000/fixtures/calendar.ics"
```

### Get home fixtures only (iCal)
```bash
curl "http://localhost:8000/fixtures/calendar.ics?venue=Tullogher"
```

### Manually refresh fixtures
```bash
curl -X POST "http://localhost:8000/fixtures/refresh"
```

## Home Assistant Integration

### Method 1: iCal Integration (Recommended)

Add this to your Home Assistant `configuration.yaml`:

```yaml
calendar:
  - platform: ical
    name: "GAA Fixtures"
    url: "http://your-gaa-api-server:8000/fixtures/calendar.ics"
```

### Method 2: With Authentication (if needed)

If Home Assistant requires authentication, set environment variables and use:

```bash
# Set auth credentials
export CALENDAR_USERNAME="gaa"
export CALENDAR_PASSWORD="fixtures123"
```

Then in Home Assistant:

```yaml
calendar:
  - platform: caldav
    url: http://your-gaa-api-server:8000/fixtures/calendar.ics
    username: gaa
    password: fixtures123
    calendars:
      - "GAA Fixtures"
```

### Separate Home/Away Calendars

```yaml
calendar:
  - platform: ical
    name: "GAA Home Games"
    url: "http://your-gaa-api-server:8000/fixtures/calendar.ics?venue=Tullogher"
  
  - platform: ical
    name: "GAA Away Games" 
    url: "http://your-gaa-api-server:8000/fixtures/calendar.ics"
```

### Troubleshooting

If you get "failed to connect":
1. **Try webcal protocol**: `webcal://your-server:8000/fixtures/calendar.ics`
2. **Check URL accessibility**: Ensure Home Assistant can reach your API server
3. **Use IP instead of hostname**: `http://192.168.1.100:8000/fixtures/calendar.ics`
4. **Enable auth if needed**: Set `CALENDAR_USERNAME` and `CALENDAR_PASSWORD` environment variables

## Database Schema

The SQLite database contains a single `fixtures` table:

```sql
CREATE TABLE fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
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

## Finding Your Club and County Board IDs

To configure the API for your club:

1. Visit your club's fixtures page on the GAA website
2. Look at the URL parameters in the AJAX request (use browser dev tools)
3. Find `clubID` and `countyBoardID` parameters
4. Set these as environment variables

Example URL format:
```
https://kilkennygaa.ie/fixtures-results/fixtures-results-ajax/?clubID=XXXX&countyBoardID=XX&fixturesOnly=Y
```

## Development

### Running Tests

With uv:
```bash
# Install dev dependencies
uv sync --extra dev

# Run tests
uv run pytest
```

With pip:
```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run tests
pytest
```

### Manual Testing

With uv:
```bash
# Test the parser directly
uv run python gaa_fixtures_parser.py

# Test with different club
CLUB_ID=1234 COUNTY_BOARD_ID=20 uv run python gaa_fixtures_parser.py
```

With pip:
```bash
# Test the parser directly
python gaa_fixtures_parser.py

# Test with different club
CLUB_ID=1234 COUNTY_BOARD_ID=20 python gaa_fixtures_parser.py
```

## Deployment

### GitHub Actions (CI/CD)

The repository includes a GitHub Action for automated deployments on releases. See `.github/workflows/deploy.yml`.

To create a release:
```bash
git tag v1.0.0
git push origin v1.0.0
```

### Production Deployment

For production deployment, consider:

- Using a reverse proxy (nginx)
- Setting up proper logging
- Using a production WSGI server (gunicorn)
- Setting up monitoring and alerting
- Regular database backups

Example with gunicorn:
```bash
pip install gunicorn
gunicorn -w 4 -k uvicorn.workers.UvicornWorker api:app
```

## Monitoring

The API includes several endpoints for monitoring:

- Health check: `GET /health`
- Metrics are available in the health response
- Docker health checks are configured

## Troubleshooting

### Common Issues

1. **No fixtures found**: Check that CLUB_ID and COUNTY_BOARD_ID are correct
2. **Database locked**: Ensure only one instance is running, or use different DB_PATH
3. **Network errors**: Check firewall and network connectivity to GAA website
4. **Parsing errors**: The GAA website may have changed format - check logs

### Logs

Enable debug logging:
```bash
export PYTHONPATH=.
python -c "import logging; logging.basicConfig(level=logging.DEBUG)"
python api.py
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes
4. Add tests
5. Submit a pull request

## License

MIT License - see LICENSE file for details.