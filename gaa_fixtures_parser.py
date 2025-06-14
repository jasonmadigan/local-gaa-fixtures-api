#!/usr/bin/env python3
"""
GAA Fixtures Parser

Fetches and parses GAA fixtures from Kilkenny GAA website.
Stores fixtures in SQLite database for caching.
"""

import sqlite3
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import json
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def parse_gaa_date(date_str: str) -> str:
    """Parse GAA date format to ISO date string for sorting"""
    try:
        # Remove ordinal suffixes (st, nd, rd, th)
        cleaned = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_str)
        
        # Parse the date
        parsed_date = datetime.strptime(cleaned, "%A %d %b %Y")
        
        # Return ISO format for easy sorting
        return parsed_date.strftime("%Y-%m-%d")
    except Exception as e:
        logger.warning(f"Could not parse date '{date_str}': {e}")
        return "9999-12-31"  # Put unparseable dates at the end

@dataclass
class Fixture:
    """Data class for GAA fixture"""
    date: str
    competition: str
    home_team: str
    away_team: str
    time: str
    venue: str
    referee: str
    raw_html: str
    created_at: str
    
    def to_dict(self) -> Dict:
        return {
            'date': self.date,
            'competition': self.competition,
            'home_team': self.home_team,
            'away_team': self.away_team,
            'time': self.time,
            'venue': self.venue,
            'referee': self.referee,
            'raw_html': self.raw_html,
            'created_at': self.created_at
        }

class GAAFixturesParser:
    """Parser for GAA fixtures from Kilkenny GAA website"""
    
    def __init__(self, db_path: str = "fixtures.db", club_id: str = "2107", county_board_id: str = "15"):
        self.db_path = db_path
        self.club_id = club_id
        self.county_board_id = county_board_id
        self.url = f"https://kilkennygaa.ie/fixtures-results/fixtures-results-ajax/?clubID={club_id}&countyBoardID={county_board_id}&fixturesOnly=Y"
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database with fixtures table"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fixtures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                date_parsed TEXT NOT NULL,
                competition TEXT NOT NULL,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                time TEXT NOT NULL,
                venue TEXT NOT NULL,
                referee TEXT NOT NULL,
                raw_html TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(date, competition, home_team, away_team, time)
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialised at {self.db_path}")
    
    def fetch_html(self) -> str:
        """Fetch HTML content from GAA website"""
        try:
            response = requests.get(self.url, timeout=30)
            response.raise_for_status()
            logger.info("Successfully fetched HTML content")
            return response.text
        except requests.RequestException as e:
            logger.error(f"Failed to fetch HTML: {e}")
            raise
    
    def parse_fixtures(self, html_content: str) -> List[Fixture]:
        """Parse HTML content to extract fixtures"""
        soup = BeautifulSoup(html_content, 'html.parser')
        fixtures = []
        
        # Find all date headers
        date_headers = soup.find_all('h3', class_='fix_res_date')
        
        for date_header in date_headers:
            date = date_header.get_text().strip()
            
            # Find all competitions after this date header
            current_element = date_header.find_next_sibling()
            
            while current_element and current_element.name != 'h3':
                if current_element.name == 'div' and 'competition' in current_element.get('class', []):
                    fixture = self._parse_competition_block(date, current_element)
                    if fixture:
                        fixtures.append(fixture)
                
                current_element = current_element.find_next_sibling()
        
        logger.info(f"Parsed {len(fixtures)} fixtures")
        return fixtures
    
    def _parse_competition_block(self, date: str, competition_div) -> Optional[Fixture]:
        """Parse a single competition block"""
        try:
            # Extract competition name
            competition_name_elem = competition_div.find('div', class_='competition-name')
            if not competition_name_elem:
                return None
            competition = competition_name_elem.get_text().strip()
            
            # Extract home team
            home_team_elem = competition_div.find('div', class_='home_team')
            home_team = ""
            if home_team_elem:
                home_team_link = home_team_elem.find('a')
                if home_team_link:
                    home_team = home_team_link.get_text().strip()
            
            # Extract away team
            away_team_elem = competition_div.find('div', class_='away_team')
            away_team = ""
            if away_team_elem:
                away_team_link = away_team_elem.find('a')
                if away_team_link:
                    away_team = away_team_link.get_text().strip()
            
            # Extract time
            time_elem = competition_div.find('div', class_='time')
            time = ""
            if time_elem:
                time = time_elem.get_text().strip()
            
            # Extract venue and referee info
            venue = ""
            referee = ""
            more_info_elem = competition_div.find('div', class_='more_info')
            if more_info_elem:
                venue_text = more_info_elem.get_text()
                
                # Extract venue
                venue_match = re.search(r'Venue:\s*([^R]+?)(?:\s+Referee:|$)', venue_text)
                if venue_match:
                    venue = venue_match.group(1).strip()
                
                # Extract referee
                referee_match = re.search(r'Referee:\s*(.+)', venue_text)
                if referee_match:
                    referee = referee_match.group(1).strip()
            
            return Fixture(
                date=date,
                competition=competition,
                home_team=home_team,
                away_team=away_team,
                time=time,
                venue=venue,
                referee=referee,
                raw_html=str(competition_div),
                created_at=datetime.now().isoformat()
            )
            
        except Exception as e:
            logger.error(f"Error parsing competition block: {e}")
            return None
    
    def save_fixtures(self, fixtures: List[Fixture]):
        """Save fixtures to SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        saved_count = 0
        for fixture in fixtures:
            try:
                date_parsed = parse_gaa_date(fixture.date)
                cursor.execute('''
                    INSERT OR IGNORE INTO fixtures 
                    (date, date_parsed, competition, home_team, away_team, time, venue, referee, raw_html, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    fixture.date, date_parsed, fixture.competition, fixture.home_team,
                    fixture.away_team, fixture.time, fixture.venue,
                    fixture.referee, fixture.raw_html, fixture.created_at
                ))
                if cursor.rowcount > 0:
                    saved_count += 1
            except sqlite3.Error as e:
                logger.error(f"Error saving fixture: {e}")
        
        conn.commit()
        conn.close()
        logger.info(f"Saved {saved_count} new fixtures to database")
    
    def get_upcoming_fixtures(self, days_ahead: int = 30) -> List[Dict]:
        """Get upcoming fixtures from database"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get fixtures from today onwards
        today = datetime.now().date()
        
        cursor.execute('''
            SELECT * FROM fixtures 
            ORDER BY date ASC
        ''')
        
        fixtures = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return fixtures
    
    def run(self):
        """Main method to fetch, parse, and save fixtures"""
        logger.info("Starting GAA fixtures parser")
        
        try:
            # Fetch HTML
            html_content = self.fetch_html()
            
            # Parse fixtures
            fixtures = self.parse_fixtures(html_content)
            
            # Save to database
            self.save_fixtures(fixtures)
            
            # Get upcoming fixtures
            upcoming = self.get_upcoming_fixtures()
            logger.info(f"Total fixtures in database: {len(upcoming)}")
            
            return upcoming
            
        except Exception as e:
            logger.error(f"Error in main execution: {e}")
            raise

def main():
    """Main entry point"""
    parser = GAAFixturesParser()
    fixtures = parser.run()
    
    # Print some upcoming fixtures
    print(f"\nFound {len(fixtures)} fixtures:")
    for fixture in fixtures[:5]:  # Show first 5
        print(f"- {fixture['date']}: {fixture['home_team']} v {fixture['away_team']} ({fixture['competition']})")

if __name__ == "__main__":
    main()