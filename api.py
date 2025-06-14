#!/usr/bin/env python3
"""
GAA Fixtures REST API

FastAPI-based REST API to serve GAA fixtures data.
Configurable via environment variables for different clubs.
"""

import os
import sqlite3
import re
from datetime import datetime, timedelta
from typing import List, Optional, Dict
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
import uvicorn
from icalendar import Calendar, Event
import secrets
from lxml import etree

from gaa_fixtures_parser import GAAFixturesParser, parse_gaa_date
# from caldav_server import CalDAVServer  # Not using the separate CalDAV server

# Configuration from environment variables
CLUB_ID = os.getenv("CLUB_ID", "2107")
COUNTY_BOARD_ID = os.getenv("COUNTY_BOARD_ID", "15")
DB_PATH = os.getenv("DB_PATH", "fixtures.db")
FETCH_INTERVAL = int(os.getenv("FETCH_INTERVAL_MINUTES", "60"))  # minutes
PORT = int(os.getenv("PORT", "8000"))

# Optional basic auth for calendar endpoint
CALENDAR_USERNAME = os.getenv("CALENDAR_USERNAME", "gaa")
CALENDAR_PASSWORD = os.getenv("CALENDAR_PASSWORD", "gaa")

# Basic auth setup
security = HTTPBasic()

def get_calendar_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify calendar credentials if auth is enabled"""
    if not CALENDAR_USERNAME or not CALENDAR_PASSWORD:
        # No auth required
        return True
    
    is_correct_username = secrets.compare_digest(credentials.username, CALENDAR_USERNAME)
    is_correct_password = secrets.compare_digest(credentials.password, CALENDAR_PASSWORD)
    
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect calendar credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials

# Pydantic models for API responses
class FixtureResponse(BaseModel):
    id: int
    date: str
    competition: str
    home_team: str
    away_team: str
    time: str
    venue: str
    referee: str
    created_at: str

class FixturesListResponse(BaseModel):
    fixtures: List[FixtureResponse]
    total_count: int
    club_id: str
    county_board_id: str

class HealthResponse(BaseModel):
    status: str
    club_id: str
    county_board_id: str
    database_path: str
    last_update: Optional[str]
    total_fixtures: int

# Global parser instance
parser: Optional[GAAFixturesParser] = None

# CalDAV server instance
# caldav_server: Optional[CalDAVServer] = None

async def fetch_fixtures_background():
    """Background task to periodically fetch fixtures"""
    global parser
    if parser:
        try:
            await asyncio.get_event_loop().run_in_executor(None, parser.run)
        except Exception as e:
            print(f"Error fetching fixtures: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global parser
    
    # Startup
    parser = GAAFixturesParser(db_path=DB_PATH, club_id=CLUB_ID, county_board_id=COUNTY_BOARD_ID)
    
    # Initial fetch
    try:
        await asyncio.get_event_loop().run_in_executor(None, parser.run)
    except Exception as e:
        print(f"Initial fetch failed: {e}")
    
    # Schedule background task
    task = asyncio.create_task(schedule_background_fetch())
    
    yield
    
    # Shutdown
    task.cancel()

async def schedule_background_fetch():
    """Schedule periodic background fetching"""
    while True:
        await asyncio.sleep(FETCH_INTERVAL * 60)  # Convert minutes to seconds
        await fetch_fixtures_background()

# Create FastAPI app
app = FastAPI(
    title="GAA Fixtures API",
    description="REST API for GAA fixtures data",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    global parser
    
    if not parser:
        raise HTTPException(status_code=503, detail="Parser not initialised")
    
    # Get fixture count and last update from database
    try:
        conn = sqlite3.connect(parser.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM fixtures")
        total_fixtures = cursor.fetchone()[0]
        
        cursor.execute("SELECT MAX(created_at) FROM fixtures")
        last_update = cursor.fetchone()[0]
        
        conn.close()
        
        return HealthResponse(
            status="healthy",
            club_id=CLUB_ID,
            county_board_id=COUNTY_BOARD_ID,
            database_path=DB_PATH,
            last_update=last_update,
            total_fixtures=total_fixtures
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/fixtures", response_model=FixturesListResponse)
async def get_fixtures(
    limit: Optional[int] = 50,
    offset: Optional[int] = 0,
    include_past: Optional[bool] = False,
    venue: Optional[str] = None
):
    """Get fixtures with optional pagination and filtering"""
    global parser
    
    if not parser:
        raise HTTPException(status_code=503, detail="Parser not initialised")
    
    try:
        conn = sqlite3.connect(parser.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Build query based on filters
        where_conditions = []
        params = []
        
        # By default, only show today and future games (exclude past games)
        if not include_past:
            today = datetime.now().strftime("%Y-%m-%d")
            where_conditions.append("date_parsed >= ?")
            params.append(today)
        
        if venue:
            where_conditions.append("venue LIKE ?")
            params.append(f"%{venue}%")
        
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)
        
        # Get total count
        count_query = f"SELECT COUNT(*) FROM fixtures {where_clause}"
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]
        
        # Get fixtures with pagination, sorted by parsed date
        query = f"""
            SELECT * FROM fixtures 
            {where_clause}
            ORDER BY date_parsed ASC, time ASC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cursor.execute(query, params)
        
        fixtures = []
        for row in cursor.fetchall():
            fixtures.append(FixtureResponse(
                id=row['id'],
                date=row['date'],
                competition=row['competition'],
                home_team=row['home_team'],
                away_team=row['away_team'],
                time=row['time'],
                venue=row['venue'],
                referee=row['referee'],
                created_at=row['created_at']
            ))
        
        conn.close()
        
        return FixturesListResponse(
            fixtures=fixtures,
            total_count=total_count,
            club_id=CLUB_ID,
            county_board_id=COUNTY_BOARD_ID
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/fixtures/calendar.ics")
async def get_fixtures_calendar(
    include_past: Optional[bool] = False,
    venue: Optional[str] = None,
    credentials = Depends(get_calendar_credentials)
):
    """Return fixtures as iCal format for CalDAV integration"""
    global parser
    
    if not parser:
        raise HTTPException(status_code=503, detail="Parser not initialised")
    
    try:
        conn = sqlite3.connect(parser.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Build query based on filters
        where_conditions = []
        params = []
        
        # By default, only show today and future games
        if not include_past:
            today = datetime.now().strftime("%Y-%m-%d")
            where_conditions.append("date_parsed >= ?")
            params.append(today)
        
        if venue:
            where_conditions.append("venue LIKE ?")
            params.append(f"%{venue}%")
        
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)
        
        # Get fixtures
        query = f"""
            SELECT * FROM fixtures 
            {where_clause}
            ORDER BY date_parsed ASC, time ASC
            LIMIT 200
        """
        cursor.execute(query, params)
        fixtures = cursor.fetchall()
        conn.close()
        
        # Create iCal calendar
        cal = Calendar()
        cal.add('prodid', f'-//GAA Fixtures API Club {CLUB_ID}//EN')
        cal.add('version', '2.0')
        cal.add('calscale', 'GREGORIAN')
        cal.add('method', 'PUBLISH')
        cal.add('x-wr-calname', f'GAA Fixtures - Club {CLUB_ID}')
        cal.add('x-wr-caldesc', f'GAA fixtures for club {CLUB_ID}')
        
        for fixture in fixtures:
            event = Event()
            
            # Event summary (title)
            summary = f"{fixture['home_team']} v {fixture['away_team']}"
            event.add('summary', summary)
            
            # Event description
            description_parts = [
                f"Competition: {fixture['competition']}",
                f"Venue: {fixture['venue']}",
                f"Referee: {fixture['referee']}"
            ]
            event.add('description', '\n'.join(description_parts))
            
            # Location
            event.add('location', fixture['venue'])
            
            # Parse date/time
            try:
                start_dt = parse_gaa_datetime(fixture['date'], fixture['time'])
                end_dt = start_dt + timedelta(hours=2)  # Assume 2-hour duration
                
                event.add('dtstart', start_dt)
                event.add('dtend', end_dt)
            except Exception as e:
                # Skip events with unparseable dates
                continue
            
            # Unique ID
            event.add('uid', f"gaa-fixture-{fixture['id']}@club-{CLUB_ID}.gaa")
            
            # Creation and modification timestamps
            now = datetime.now()
            event.add('dtstamp', now)
            event.add('created', datetime.fromisoformat(fixture['created_at'].replace('Z', '+00:00')))
            event.add('last-modified', now)
            
            # Categories
            event.add('categories', ['GAA', 'Hurling', 'Football', fixture['competition'].split()[0]])
            
            cal.add_component(event)
        
        # Return iCal content
        ical_content = cal.to_ical().decode('utf-8')
        
        return Response(
            content=ical_content,
            media_type="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=gaa-fixtures-club-{CLUB_ID}.ics",
                "Cache-Control": "max-age=3600"  # Cache for 1 hour
            }
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Calendar generation error: {str(e)}")

@app.get("/fixtures/venues")
async def get_venues():
    """Get list of available venues"""
    global parser
    
    if not parser:
        raise HTTPException(status_code=503, detail="Parser not initialised")
    
    try:
        conn = sqlite3.connect(parser.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT DISTINCT venue FROM fixtures WHERE venue != '' ORDER BY venue")
        venues = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        
        return {"venues": venues}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/fixtures/competitions")
async def get_competitions():
    """Get list of available competitions"""
    global parser
    
    if not parser:
        raise HTTPException(status_code=503, detail="Parser not initialised")
    
    try:
        conn = sqlite3.connect(parser.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT DISTINCT competition FROM fixtures ORDER BY competition")
        competitions = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        
        return {"competitions": competitions}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/fixtures/by-venue/{venue}")
async def get_fixtures_by_venue(
    venue: str,
    limit: Optional[int] = 50,
    offset: Optional[int] = 0,
    include_past: Optional[bool] = False
):
    """Get fixtures for a specific venue"""
    global parser
    
    if not parser:
        raise HTTPException(status_code=503, detail="Parser not initialised")
    
    try:
        conn = sqlite3.connect(parser.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Build where conditions
        where_conditions = ["venue LIKE ?"]
        params = [f"%{venue}%"]
        
        # By default, only show today and future games (exclude past games)
        if not include_past:
            today = datetime.now().strftime("%Y-%m-%d")
            where_conditions.append("date_parsed >= ?")
            params.append(today)
        
        where_clause = "WHERE " + " AND ".join(where_conditions)
        
        # Get total count
        cursor.execute(f"SELECT COUNT(*) FROM fixtures {where_clause}", params)
        total_count = cursor.fetchone()[0]
        
        if total_count == 0:
            raise HTTPException(status_code=404, detail="No fixtures found for this venue")
        
        # Get fixtures
        cursor.execute(f"""
            SELECT * FROM fixtures 
            {where_clause}
            ORDER BY date_parsed ASC, time ASC
            LIMIT ? OFFSET ?
        """, params + [limit, offset])
        
        fixtures = []
        for row in cursor.fetchall():
            fixtures.append(FixtureResponse(
                id=row['id'],
                date=row['date'],
                competition=row['competition'],
                home_team=row['home_team'],
                away_team=row['away_team'],
                time=row['time'],
                venue=row['venue'],
                referee=row['referee'],
                created_at=row['created_at']
            ))
        
        conn.close()
        
        return FixturesListResponse(
            fixtures=fixtures,
            total_count=total_count,
            club_id=CLUB_ID,
            county_board_id=COUNTY_BOARD_ID
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/fixtures/by-competition/{competition}")
async def get_fixtures_by_competition(
    competition: str,
    limit: Optional[int] = 50,
    offset: Optional[int] = 0,
    include_past: Optional[bool] = False
):
    """Get fixtures for a specific competition"""
    global parser
    
    if not parser:
        raise HTTPException(status_code=503, detail="Parser not initialised")
    
    try:
        conn = sqlite3.connect(parser.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Build where conditions
        where_conditions = ["competition = ?"]
        params = [competition]
        
        # By default, only show today and future games (exclude past games)
        if not include_past:
            today = datetime.now().strftime("%Y-%m-%d")
            where_conditions.append("date_parsed >= ?")
            params.append(today)
        
        where_clause = "WHERE " + " AND ".join(where_conditions)
        
        # Get total count
        cursor.execute(f"SELECT COUNT(*) FROM fixtures {where_clause}", params)
        total_count = cursor.fetchone()[0]
        
        if total_count == 0:
            raise HTTPException(status_code=404, detail="No upcoming fixtures found for this competition")
        
        # Get fixtures
        cursor.execute(f"""
            SELECT * FROM fixtures 
            {where_clause}
            ORDER BY date_parsed ASC, time ASC
            LIMIT ? OFFSET ?
        """, params + [limit, offset])
        
        fixtures = []
        for row in cursor.fetchall():
            fixtures.append(FixtureResponse(
                id=row['id'],
                date=row['date'],
                competition=row['competition'],
                home_team=row['home_team'],
                away_team=row['away_team'],
                time=row['time'],
                venue=row['venue'],
                referee=row['referee'],
                created_at=row['created_at']
            ))
        
        conn.close()
        
        return FixturesListResponse(
            fixtures=fixtures,
            total_count=total_count,
            club_id=CLUB_ID,
            county_board_id=COUNTY_BOARD_ID
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/fixtures/{fixture_id}", response_model=FixtureResponse)
async def get_fixture(fixture_id: int):
    """Get a specific fixture by ID"""
    global parser
    
    if not parser:
        raise HTTPException(status_code=503, detail="Parser not initialised")
    
    try:
        conn = sqlite3.connect(parser.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM fixtures WHERE id = ?", (fixture_id,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Fixture not found")
        
        conn.close()
        
        return FixtureResponse(
            id=row['id'],
            date=row['date'],
            competition=row['competition'],
            home_team=row['home_team'],
            away_team=row['away_team'],
            time=row['time'],
            venue=row['venue'],
            referee=row['referee'],
            created_at=row['created_at']
        )
        
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/fixtures/refresh")
async def refresh_fixtures(background_tasks: BackgroundTasks):
    """Manually trigger fixtures refresh"""
    global parser
    
    if not parser:
        raise HTTPException(status_code=503, detail="Parser not initialised")
    
    background_tasks.add_task(fetch_fixtures_background)
    
    return {"message": "Fixtures refresh triggered"}

def parse_gaa_datetime(date_str: str, time_str: str) -> datetime:
    """Parse GAA date/time format to datetime object"""
    try:
        # Remove ordinal suffixes (st, nd, rd, th)
        cleaned = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_str)
        
        # Extract just the date part (remove day name)
        parts = cleaned.split()
        if len(parts) >= 4:  # ["Sunday", "15", "Jun", "2025"]
            date_part = ' '.join(parts[1:])  # "15 Jun 2025"
        else:
            date_part = cleaned
        
        # Combine date and time
        full_datetime = f"{date_part} {time_str}"
        
        # Parse the datetime
        return datetime.strptime(full_datetime, "%d %b %Y %H:%M")
        
    except Exception as e:
        # Fallback: use the parsed date from database
        iso_date = parse_gaa_date(date_str)
        time_parts = time_str.split(':')
        hour = int(time_parts[0])
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0
        
        date_obj = datetime.strptime(iso_date, "%Y-%m-%d")
        return date_obj.replace(hour=hour, minute=minute)

# CalDAV endpoints - simplified for debugging
@app.api_route("/fixtures/calendar.ics", methods=["PROPFIND"])
async def caldav_propfind(request: Request, credentials = Depends(get_calendar_credentials)):
    """Handle CalDAV PROPFIND requests"""
    try:
        # Create simple PROPFIND response
        multistatus = etree.Element("{DAV:}multistatus")
        multistatus.set("xmlns", "DAV:")
        multistatus.set("{http://www.w3.org/2000/xmlns/}C", "urn:ietf:params:xml:ns:caldav")
        
        response_elem = etree.SubElement(multistatus, "{DAV:}response")
        href = etree.SubElement(response_elem, "{DAV:}href")
        href.text = "/fixtures/calendar.ics"
        
        propstat = etree.SubElement(response_elem, "{DAV:}propstat")
        prop = etree.SubElement(propstat, "{DAV:}prop")
        
        # Add calendar properties
        resourcetype = etree.SubElement(prop, "{DAV:}resourcetype")
        calendar = etree.SubElement(resourcetype, "{urn:ietf:params:xml:ns:caldav}calendar")
        
        displayname = etree.SubElement(prop, "{DAV:}displayname")
        displayname.text = f"GAA Fixtures - Club {CLUB_ID}"
        
        status = etree.SubElement(propstat, "{DAV:}status")
        status.text = "HTTP/1.1 200 OK"
        
        xml_content = etree.tostring(multistatus, encoding='utf-8', xml_declaration=True, pretty_print=True)
        
        return Response(
            content=xml_content,
            media_type="application/xml; charset=utf-8",
            headers={
                "DAV": "1, 2, 3, calendar-access",
                "Content-Type": "application/xml; charset=utf-8"
            }
        )
    except Exception as e:
        print(f"PROPFIND error: {e}")
        raise HTTPException(status_code=500, detail=f"PROPFIND error: {str(e)}")

@app.api_route("/fixtures/calendar.ics", methods=["OPTIONS"])
async def caldav_options(request: Request):
    """Handle CalDAV OPTIONS requests"""
    return Response(
        content="",
        headers={
            "DAV": "1, 2, 3, calendar-access",
            "Allow": "GET, HEAD, PUT, PROPFIND, OPTIONS, REPORT",
            "Content-Type": "text/html; charset=utf-8"
        }
    )

@app.api_route("/fixtures/calendar.ics", methods=["REPORT"])
async def caldav_report(request: Request, credentials = Depends(get_calendar_credentials)):
    """Handle CalDAV REPORT requests"""
    try:
        # Get calendar data
        calendar_response = await get_fixtures_calendar(include_past=False, venue=None, credentials=credentials)
        if hasattr(calendar_response, 'body'):
            ical_content = calendar_response.body.decode('utf-8')
        else:
            ical_content = calendar_response.content.decode('utf-8')
        
        # Create REPORT response
        multistatus = etree.Element("{DAV:}multistatus")
        multistatus.set("xmlns", "DAV:")
        multistatus.set("{http://www.w3.org/2000/xmlns/}C", "urn:ietf:params:xml:ns:caldav")
        
        response_elem = etree.SubElement(multistatus, "{DAV:}response")
        href = etree.SubElement(response_elem, "{DAV:}href")
        href.text = "/fixtures/calendar.ics"
        
        propstat = etree.SubElement(response_elem, "{DAV:}propstat")
        prop = etree.SubElement(propstat, "{DAV:}prop")
        
        etag = etree.SubElement(prop, "{DAV:}getetag")
        etag.text = f'"gaa-fixtures-{CLUB_ID}"'
        
        caldata = etree.SubElement(prop, "{urn:ietf:params:xml:ns:caldav}calendar-data")
        caldata.text = ical_content
        
        status = etree.SubElement(propstat, "{DAV:}status")
        status.text = "HTTP/1.1 200 OK"
        
        xml_content = etree.tostring(multistatus, encoding='utf-8', xml_declaration=True, pretty_print=True)
        
        return Response(
            content=xml_content,
            media_type="application/xml; charset=utf-8",
            headers={
                "DAV": "1, 2, 3, calendar-access",
                "Content-Type": "application/xml; charset=utf-8"
            }
        )
        
    except Exception as e:
        print(f"REPORT error: {e}")
        # Return empty response on error
        multistatus = etree.Element("{DAV:}multistatus")
        multistatus.set("xmlns", "DAV:")
        xml_content = etree.tostring(multistatus, encoding='utf-8', xml_declaration=True)
        
        return Response(
            content=xml_content,
            media_type="application/xml; charset=utf-8",
            headers={
                "DAV": "1, 2, 3, calendar-access"
            }
        )

# Principal/user discovery endpoints
@app.api_route("/", methods=["PROPFIND"])
@app.api_route("/fixtures/", methods=["PROPFIND"])
async def caldav_root_propfind(request: Request, credentials = Depends(get_calendar_credentials)):
    """Handle root PROPFIND for CalDAV discovery"""
    try:
        multistatus = etree.Element("{DAV:}multistatus")
        multistatus.set("xmlns", "DAV:")
        multistatus.set("{http://www.w3.org/2000/xmlns/}C", "urn:ietf:params:xml:ns:caldav")
        
        response_elem = etree.SubElement(multistatus, "{DAV:}response")
        href = etree.SubElement(response_elem, "{DAV:}href")
        href.text = "/"
        
        propstat = etree.SubElement(response_elem, "{DAV:}propstat")
        prop = etree.SubElement(propstat, "{DAV:}prop")
        
        principal = etree.SubElement(prop, "{DAV:}current-user-principal")
        principal_href = etree.SubElement(principal, "{DAV:}href")
        principal_href.text = "/fixtures/calendar.ics"
        
        resourcetype = etree.SubElement(prop, "{DAV:}resourcetype")
        collection = etree.SubElement(resourcetype, "{DAV:}collection")
        
        status = etree.SubElement(propstat, "{DAV:}status")
        status.text = "HTTP/1.1 200 OK"
        
        xml_content = etree.tostring(multistatus, encoding='utf-8', xml_declaration=True, pretty_print=True)
        
        return Response(
            content=xml_content,
            media_type="application/xml; charset=utf-8",
            headers={
                "DAV": "1, 2, 3, calendar-access",
                "Content-Type": "application/xml; charset=utf-8"
            }
        )
    except Exception as e:
        print(f"Root PROPFIND error: {e}")
        raise HTTPException(status_code=500, detail=f"Root PROPFIND error: {str(e)}")


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=PORT,
        reload=False
    )