"""
Web scraper module for archery results.
Parses HTML from the archery results database website.
"""

import re
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass
import html
import logging

logger = logging.getLogger(__name__)


@dataclass
class ArcherInfo:
    """Parsed archer information from detail page."""
    external_id: int
    name: str
    club: Optional[str] = None
    club_id: Optional[int] = None
    profile_url: Optional[str] = None
    years_available: List[int] = None
    current_year_results: List[Dict[str, Any]] = None
    stats: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.years_available is None:
            self.years_available = []
        if self.current_year_results is None:
            self.current_year_results = []
        if self.stats is None:
            self.stats = {}

# Norwegian month mapping
NORWEGIAN_MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'mai': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'okt': 10, 'nov': 11, 'des': 12
}


def parse_norwegian_date(date_str: str, year_str: str = None) -> str:
    """
    Parse Norwegian date format (e.g., '05 des. 25') to ISO format.
    Returns date in YYYY-MM-DD format.
    """
    try:
        # Clean the date string
        date_str = date_str.strip().lower()
        
        # Extract parts: "05 des. 25" -> day=05, month=des, year=25
        parts = date_str.replace('.', '').split()
        
        if len(parts) >= 3:
            day = int(parts[0])
            month_str = parts[1][:3]  # Take first 3 chars
            year = int(parts[2])
        elif len(parts) == 2 and year_str:
            day = int(parts[0])
            month_str = parts[1][:3]
            year = int(year_str)
        else:
            return date_str  # Return original if can't parse
        
        # Convert 2-digit year to 4-digit
        if year < 100:
            year = 2000 + year
        
        month = NORWEGIAN_MONTHS.get(month_str, 1)
        
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, IndexError) as e:
        logger.warning(f"Error parsing date '{date_str}': {e}")
        return date_str


def parse_result_html(html_content: str) -> List[Dict[str, Any]]:
    """
    Parse HTML content containing archery results.
    
    Expected HTML structure:
    <div class="fRow datarows" onclick="location.href='/Event/Result/2025230'" title="Event Name">
        <div class="flexDate">
            <span>05 des. 25</span>
            <span class="hidden">2025</span>
        </div>
        <div class="flexSubRows">
            <div class="flexSubRowItem">Event Name</div>
            <div class="flexSubRowItem">
                <div class="flexItems">18 m (30 piler)</div>  <!-- Distance -->
                <div class="flexItems">C1</div>               <!-- Category -->
                <div class="flexItemsInfo">282</div>          <!-- Score -->
                <div class="flexItemsInfo">1</div>            <!-- Placement -->
            </div>
        </div>
    </div>
    """
    results = []
    soup = BeautifulSoup(html_content, 'lxml')
    
    # Find all result rows
    rows = soup.find_all('div', class_='fRow datarows')
    
    for row in rows:
        try:
            result = parse_single_result(row)
            if result:
                results.append(result)
        except Exception as e:
            logger.warning(f"Error parsing row: {e}")
            continue
    
    return results


def parse_single_result(row) -> Optional[Dict[str, Any]]:
    """Parse a single result row."""
    result = {}
    
    # Extract event URL and ID from onclick attribute
    onclick = row.get('onclick', '')
    event_url_match = re.search(r"location\.href='(/Event/Result/(\d+))'", onclick)
    if event_url_match:
        result['event_url'] = event_url_match.group(1)
        result['event_id'] = event_url_match.group(2)
    
    # Extract event name from title attribute
    result['event_name'] = html.unescape(row.get('title', ''))
    
    # Extract date
    date_div = row.find('div', class_='flexDate')
    if date_div:
        date_spans = date_div.find_all('span')
        if len(date_spans) >= 1:
            date_str = date_spans[0].get_text(strip=True)
            year_str = None
            if len(date_spans) >= 2:
                year_str = date_spans[1].get_text(strip=True)
            result['date'] = parse_norwegian_date(date_str, year_str)
            result['date_raw'] = date_str
    
    # Extract sub-row items (event name, distance, category, score, placement)
    sub_rows = row.find('div', class_='flexSubRows')
    if sub_rows:
        sub_items = sub_rows.find_all('div', class_='flexSubRowItem')
        
        if len(sub_items) >= 2:
            # Second sub-item contains distance, category, score, placement
            details_div = sub_items[1]
            
            # Get flexItems (distance and category)
            flex_items = details_div.find_all('div', class_='flexItems')
            if len(flex_items) >= 1:
                result['distance'] = flex_items[0].get_text(strip=True)
            if len(flex_items) >= 2:
                result['category'] = flex_items[1].get_text(strip=True)
            
            # Get flexItemsInfo (score and placement)
            flex_info = details_div.find_all('div', class_='flexItemsInfo')
            if len(flex_info) >= 1:
                try:
                    result['score'] = int(flex_info[0].get_text(strip=True))
                except ValueError:
                    result['score'] = 0
            if len(flex_info) >= 2:
                try:
                    result['placement'] = int(flex_info[1].get_text(strip=True))
                except ValueError:
                    result['placement'] = None
    
    # Validate required fields
    required_fields = ['event_id', 'date', 'distance', 'category', 'score']
    if all(field in result for field in required_fields):
        return result
    
    return None


def scrape_archer_results(base_url: str, archer_profile_url: str, session: requests.Session = None) -> List[Dict[str, Any]]:
    """
    Scrape results for an archer from their profile page.
    
    Args:
        base_url: Base URL of the website (e.g., 'https://example.com')
        archer_profile_url: URL path to archer's profile (e.g., '/Archer/123')
        session: Optional requests session for maintaining cookies
    
    Returns:
        List of parsed results
    """
    if session is None:
        session = requests.Session()
    
    full_url = base_url.rstrip('/') + '/' + archer_profile_url.lstrip('/')
    
    try:
        response = session.get(full_url, timeout=30)
        response.raise_for_status()
        return parse_result_html(response.text)
    except requests.RequestException as e:
        print(f"Error fetching {full_url}: {e}")
        return []


def parse_html_file(file_path: str) -> List[Dict[str, Any]]:
    """
    Parse results from a local HTML file.
    Useful for testing with downloaded HTML.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return parse_result_html(content)


# Distance normalization for better grouping
DISTANCE_NORMALIZATIONS = {
    '18 m': '18m Indoor',
    '18 m (30 piler)': '18m Indoor (30 arrows)',
    '25 m': '25m Indoor',
    '720-runde': '720 Round (70m)',
    'Norsk kortrunde': 'Norwegian Short Round',
}


def normalize_distance(distance: str) -> str:
    """Normalize distance strings for consistent grouping."""
    return DISTANCE_NORMALIZATIONS.get(distance, distance)


# Category descriptions
CATEGORY_DESCRIPTIONS = {
    'C1': 'Compound Men',
    'CH': 'Compound Men Senior',
    'R1': 'Recurve Men',
    'RH': 'Recurve Men Senior',
    'RH5': 'Recurve Men 50+',
    'R40': 'Recurve Men 40+',
    'C2': 'Compound Women',
    'R2': 'Recurve Women',
}


def get_category_description(category: str) -> str:
    """Get human-readable description for a category code."""
    return CATEGORY_DESCRIPTIONS.get(category, category)


def parse_archer_page(html_content: str, external_id: int) -> Optional[ArcherInfo]:
    """
    Parse a complete archer detail page.
    
    Extracts:
    - Archer name
    - Club name and ID
    - Available years for historical data
    - Current season results
    - Basic statistics
    
    Args:
        html_content: HTML content of the archer's detail page
        external_id: The external ID of the archer
        
    Returns:
        ArcherInfo object or None if parsing fails
    """
    try:
        soup = BeautifulSoup(html_content, 'lxml')
        
        # Extract archer name from <h3 class="title">
        name_elem = soup.find('h3', class_='title')
        if not name_elem:
            logger.warning(f"Could not find archer name for ID {external_id}")
            return None
        
        name = name_elem.get_text(strip=True)

        # Verify external ID from hidden input.
        # The site returns HTTP 200 with current_archer=0 and an empty name
        # for non-existent archer IDs, so we treat that as "not found".
        id_input = soup.find('input', id='current_archer')
        if id_input:
            page_id = int(id_input.get('value', 0))
            if page_id == 0:
                logger.debug(f"Archer ID {external_id} does not exist on site (current_archer=0)")
                return None
            if page_id != external_id:
                logger.warning(f"External ID mismatch: expected {external_id}, got {page_id}")

        if not name:
            logger.debug(f"Archer ID {external_id}: empty name, skipping")
            return None
        
        # Extract club info
        club = None
        club_id = None
        club_link = soup.find('a', href=re.compile(r'/Club/Detail/\d+'))
        if club_link:
            club = club_link.get_text(strip=True).replace('\xa0', ' ').strip()
            # Remove the link icon text if present
            club = re.sub(r'\s*$', '', club)
            club_match = re.search(r'/Club/Detail/(\d+)', club_link.get('href', ''))
            if club_match:
                club_id = int(club_match.group(1))
        
        # Extract available years from the historical dropdown
        years_available = []
        year_select = soup.find('select', id='previousyear')
        if year_select:
            for option in year_select.find_all('option'):
                try:
                    year = int(option.get('value', 0))
                    if year > 0:
                        years_available.append(year)
                except ValueError:
                    continue
        
        # Parse current season results
        current_results = parse_result_html(html_content)
        
        # Extract basic stats from the stats table
        stats = _extract_stats(soup)
        
        return ArcherInfo(
            external_id=external_id,
            name=name,
            club=club,
            club_id=club_id,
            profile_url=f"/Archer/Details/{external_id}",
            years_available=sorted(years_available, reverse=True),
            current_year_results=current_results,
            stats=stats
        )
        
    except Exception as e:
        logger.error(f"Error parsing archer page for ID {external_id}: {e}")
        return None


def _extract_stats(soup: BeautifulSoup) -> Dict[str, Any]:
    """Extract statistics from the archer page."""
    stats = {}
    
    try:
        # Find the stats table (flexTable with Starter, Top 3, Seire headers)
        flex_tables = soup.find_all('div', class_='flexTable')
        
        for table in flex_tables:
            # Look for the stats table with "Starter" header
            header_row = table.find('div', class_='fRow noHiglight fItemsBasis')
            if not header_row:
                continue
            
            header_items = header_row.find_all('div', class_='flexItems')
            if len(header_items) < 4:
                continue
            
            # Check if this is the stats table
            headers = [item.get_text(strip=True) for item in header_items]
            if 'Starter' not in headers:
                continue
            
            # Parse data rows
            data_rows = table.find_all('div', class_='fRow fItemsBasis')
            for row in data_rows:
                items = row.find_all('div', class_='flexItems')
                if len(items) < 4:
                    continue
                
                label = items[0].get_text(strip=True)
                try:
                    starts = int(items[1].get_text(strip=True))
                    top3 = int(items[2].get_text(strip=True))
                    wins = int(items[3].get_text(strip=True))
                    
                    if label.isdigit():  # Current year
                        stats['current_year'] = {
                            'year': int(label),
                            'starts': starts,
                            'top3': top3,
                            'wins': wins
                        }
                    elif label.lower() == 'totalt':
                        stats['total'] = {
                            'starts': starts,
                            'top3': top3,
                            'wins': wins
                        }
                except ValueError:
                    continue
            
            break  # Found the stats table, no need to continue
    
    except Exception as e:
        logger.debug(f"Error extracting stats: {e}")
    
    return stats


def parse_historical_results_html(html_content: str) -> List[Dict[str, Any]]:
    """
    Parse historical results from AJAX response.
    
    The historical data may be loaded differently depending on how the
    website implements it. This function handles the HTML fragment
    returned by the AJAX call.
    
    Args:
        html_content: HTML content (may be a fragment or full page)
        
    Returns:
        List of parsed results
    """
    # The historical results use the same structure as current results
    return parse_result_html(html_content)


def check_archer_exists(html_content: str) -> bool:
    """
    Check if an archer page contains valid archer data.
    
    Args:
        html_content: HTML content of the page
        
    Returns:
        True if the page contains valid archer data, False otherwise
    """
    if not html_content:
        return False
    
    soup = BeautifulSoup(html_content, 'lxml')
    
    # Check for archer name
    name_elem = soup.find('h3', class_='title')
    if not name_elem:
        return False
    
    # Check for the current_archer hidden input
    id_input = soup.find('input', id='current_archer')
    if not id_input:
        return False
    
    return True


def get_latest_result_date(results: List[Dict[str, Any]]) -> Optional[str]:
    """
    Get the most recent result date from a list of results.
    
    Args:
        results: List of parsed results
        
    Returns:
        Most recent date in YYYY-MM-DD format, or None if no results
    """
    if not results:
        return None
    
    dates = [r.get('date') for r in results if r.get('date')]
    if not dates:
        return None
    
    # Sort dates and return the most recent
    return max(dates)


if __name__ == '__main__':
    # Test with sample file
    import sys
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        
        # Try to extract archer ID from filename if present
        id_match = re.search(r'(\d+)', file_path)
        archer_id = int(id_match.group(1)) if id_match else 0
        
        # Read and parse the file
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse as full archer page
        archer_info = parse_archer_page(content, archer_id)
        
        if archer_info:
            print(f"Archer: {archer_info.name}")
            print(f"Club: {archer_info.club} (ID: {archer_info.club_id})")
            print(f"Years available: {archer_info.years_available}")
            print(f"Stats: {archer_info.stats}")
            print(f"\nResults ({len(archer_info.current_year_results)}):")
            for r in archer_info.current_year_results:
                print(f"  {r['date']} - {r['event_name']}: {r['score']} ({r['category']}, {r['distance']})")
        else:
            print("Failed to parse archer page")
            # Fall back to just parsing results
            results = parse_result_html(content)
            print(f"\nFound {len(results)} results:")
            for r in results:
                print(f"  {r['date']} - {r['event_name']}: {r['score']} ({r['category']}, {r['distance']})")
