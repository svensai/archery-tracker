"""
Database module for archery results storage.

Uses SQLite by default. Set the DATABASE_URL environment variable to a
PostgreSQL connection string to use PostgreSQL instead:

    export DATABASE_URL=postgresql://user:password@localhost:5432/archery
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'archery.db')

# Number of arrows shot in each competition format.
# Used to normalise scores to a "per-60-arrows" basis for fair comparison.
ARROWS_BY_DISTANCE: dict = {
    '18 m':            60,
    '18 m (30 piler)': 30,   # kortere innendørsrunde
    '25 m':            60,
    '720-runde':       72,
    '720 runde':       72,
    '2 x 720 runde':   144,  # dobbelt 720-runde
    '1440-runde':      144,
    '1440 runde':      144,
    '1/2 1440-runde':  72,   # halv 1440-runde, art. 1f)
    '900':             90,
    '900-runde':       90,
    'Skandiarunde':    90,   # = 900-runde, 30p × 3 distanser, art. 1g)
    '120 p. 25/18 m':  120,  # innendørs 120-pilers kombinasjonsrunde
    'Norgesrunde':     60,   # Regelverk B, art. 103
    '50 m':            72,
    '70 m':            72,
    '60 m':            72,
    '90 m':            36,
}


# Maximum achievable score per format (10 pts/arrow).
# Only formats in ARROWS_BY_DISTANCE can be validated; unknown formats are excluded.
MAX_SCORE_BY_DISTANCE: dict = {dist: arrows * 10 for dist, arrows in ARROWS_BY_DISTANCE.items()}


def score_per_60(score: int, distance: str) -> Optional[float]:
    """Return score normalised to a 60-arrow basis.

    Returns None for formats without a known arrow count (3D, felt, ...) —
    pretending they are 60-arrow rounds would make them look comparable
    to target rounds when they are not.
    """
    arrows = ARROWS_BY_DISTANCE.get(distance)
    if arrows is None:
        return None
    return round(score / arrows * 60, 1)


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')  # Better concurrency
    return conn


def init_database():
    """Initialize the database with required tables."""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Create archers table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS archers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            external_id INTEGER,
            club TEXT,
            club_id INTEGER,
            profile_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create events table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            event_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create results table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archer_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            distance TEXT NOT NULL,
            category TEXT NOT NULL,
            score INTEGER NOT NULL,
            placement INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (archer_id) REFERENCES archers(id),
            FOREIGN KEY (event_id) REFERENCES events(id),
            UNIQUE(archer_id, event_id, distance, category)
        )
    ''')
    
    # Create sync_status table - tracks sync state for each external archer
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_status (
            external_id INTEGER PRIMARY KEY,
            archer_id INTEGER,
            name TEXT,
            club TEXT,
            club_id INTEGER,
            exists_online BOOLEAN DEFAULT 1,
            historical_synced BOOLEAN DEFAULT 0,
            last_sync TIMESTAMP,
            last_result_date TEXT,
            total_results INTEGER DEFAULT 0,
            years_available TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (archer_id) REFERENCES archers(id)
        )
    ''')
    
    # Create sync_errors table - outbox pattern for error tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id INTEGER,
            error_type TEXT,
            error_message TEXT,
            http_status INTEGER,
            request_url TEXT,
            response_body TEXT,
            retry_count INTEGER DEFAULT 0,
            resolved BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        )
    ''')
    
    # Create sync_log table - tracks sync job runs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            archers_processed INTEGER DEFAULT 0,
            results_added INTEGER DEFAULT 0,
            errors_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            details TEXT,
            total INTEGER
        )
    ''')

    # Migrate existing databases: add columns introduced after initial schema
    _migrations = [
        'ALTER TABLE archers ADD COLUMN external_id INTEGER',
        'ALTER TABLE archers ADD COLUMN club TEXT',
        'ALTER TABLE archers ADD COLUMN club_id INTEGER',
        'ALTER TABLE sync_log ADD COLUMN total INTEGER',
    ]
    for _sql in _migrations:
        try:
            cursor.execute(_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Create indexes for faster queries
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_results_archer ON results(archer_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_results_event ON results(event_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_results_date ON results(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_results_category ON results(category)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_archers_external_id ON archers(external_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_status_last_sync ON sync_status(last_sync)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_errors_resolved ON sync_errors(resolved)')
    
    conn.commit()
    conn.close()
    print(f"Database initialized at: {DATABASE_PATH}")


def add_archer(name: str, profile_url: Optional[str] = None) -> int:
    """Add a new archer or get existing archer ID."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            'INSERT INTO archers (name, profile_url) VALUES (?, ?)',
            (name, profile_url)
        )
        archer_id = cursor.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        # Archer already exists, get their ID
        cursor.execute('SELECT id FROM archers WHERE name = ?', (name,))
        archer_id = cursor.fetchone()['id']
        # Update profile URL if provided
        if profile_url:
            cursor.execute(
                'UPDATE archers SET profile_url = ?, updated_at = ? WHERE id = ?',
                (profile_url, datetime.now(), archer_id)
            )
            conn.commit()
    
    conn.close()
    return archer_id


def add_event(event_id: str, name: str, event_url: Optional[str] = None) -> int:
    """Add a new event or get existing event ID."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            'INSERT INTO events (event_id, name, event_url) VALUES (?, ?, ?)',
            (event_id, name, event_url)
        )
        db_event_id = cursor.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        cursor.execute('SELECT id FROM events WHERE event_id = ?', (event_id,))
        db_event_id = cursor.fetchone()['id']
    
    conn.close()
    return db_event_id


def add_result(
    archer_id: int,
    event_id: int,
    date: str,
    distance: str,
    category: str,
    score: int,
    placement: Optional[int] = None
) -> int:
    """Add a competition result."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO results (archer_id, event_id, date, distance, category, score, placement)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (archer_id, event_id, date, distance, category, score, placement))
        result_id = cursor.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        # Result already exists, update it
        cursor.execute('''
            UPDATE results 
            SET score = ?, placement = ?, date = ?
            WHERE archer_id = ? AND event_id = ? AND distance = ? AND category = ?
        ''', (score, placement, date, archer_id, event_id, distance, category))
        cursor.execute('''
            SELECT id FROM results 
            WHERE archer_id = ? AND event_id = ? AND distance = ? AND category = ?
        ''', (archer_id, event_id, distance, category))
        result_id = cursor.fetchone()['id']
        conn.commit()
    
    conn.close()
    return result_id


def get_all_archers() -> List[Dict[str, Any]]:
    """Get all archers."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM archers ORDER BY name')
    archers = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return archers


def get_archer_results(
    archer_id: int,
    category: Optional[str] = None,
    distance: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get all results for an archer with optional filtering."""
    conn = get_connection()
    cursor = conn.cursor()

    query = '''
        SELECT r.*, e.name as event_name, e.event_id as external_event_id, a.name as archer_name
        FROM results r
        JOIN events e ON r.event_id = e.id
        JOIN archers a ON r.archer_id = a.id
        WHERE r.archer_id = ?
    '''
    params = [archer_id]

    if category:
        query += ' AND r.category = ?'
        params.append(category)
    if distance:
        query += ' AND r.distance = ?'
        params.append(distance)
    if date_from:
        query += ' AND r.date >= ?'
        params.append(date_from)
    if date_to:
        query += ' AND r.date <= ?'
        params.append(date_to)

    query += ' ORDER BY r.date DESC'

    cursor.execute(query, params)
    results = []
    for row in cursor.fetchall():
        r = dict(row)
        r['score_per_60'] = score_per_60(r['score'], r['distance'])
        results.append(r)
    conn.close()
    return results


def get_all_results(
    category: Optional[str] = None,
    distance: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get all results with optional filtering."""
    conn = get_connection()
    cursor = conn.cursor()

    query = '''
        SELECT r.*, e.name as event_name, e.event_id as external_event_id, a.name as archer_name
        FROM results r
        JOIN events e ON r.event_id = e.id
        JOIN archers a ON r.archer_id = a.id
        WHERE 1=1
    '''
    params = []

    if category:
        query += ' AND r.category = ?'
        params.append(category)
    if distance:
        query += ' AND r.distance = ?'
        params.append(distance)
    if date_from:
        query += ' AND r.date >= ?'
        params.append(date_from)
    if date_to:
        query += ' AND r.date <= ?'
        params.append(date_to)

    query += ' ORDER BY r.date DESC'

    cursor.execute(query, params)
    results = []
    for row in cursor.fetchall():
        r = dict(row)
        r['score_per_60'] = score_per_60(r['score'], r['distance'])
        results.append(r)
    conn.close()
    return results


def get_flagged_results() -> List[Dict[str, Any]]:
    """Return results whose score exceeds the theoretical maximum for their format.

    Only formats listed in ARROWS_BY_DISTANCE are checked; unknown formats
    (e.g. 3D, felt) are excluded because their ceiling is undefined.
    Each returned row includes 'max_score' (the threshold that was breached).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT r.*, e.name as event_name, e.event_id as external_event_id, a.name as archer_name
        FROM results r
        JOIN events e ON r.event_id = e.id
        JOIN archers a ON r.archer_id = a.id
        ORDER BY r.date DESC
    ''')
    flagged = []
    for row in cursor.fetchall():
        r = dict(row)
        max_score = MAX_SCORE_BY_DISTANCE.get(r['distance'])
        if max_score is not None and r['score'] > max_score:
            r['max_score'] = max_score
            r['score_per_60'] = score_per_60(r['score'], r['distance'])
            flagged.append(r)
    conn.close()
    return flagged


def get_active_archers_per_year(categories: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return count of distinct active archers per year.

    If categories is non-empty, returns one dataset per category.
    If categories is empty/None, returns a single 'Totalt' dataset.
    Also includes a flag indicating whether historical data is incomplete.
    """
    conn = get_connection()
    cursor = conn.cursor()

    if categories:
        placeholders = ','.join('?' * len(categories))
        cursor.execute(f'''
            SELECT strftime('%Y', date) as year, category,
                   COUNT(DISTINCT archer_id) as count
            FROM results
            WHERE category IN ({placeholders})
            GROUP BY year, category
            ORDER BY year, category
        ''', categories)
        rows = [dict(r) for r in cursor.fetchall()]
        years = sorted(set(r['year'] for r in rows))
        datasets = []
        for cat in categories:
            cat_data = {r['year']: r['count'] for r in rows if r['category'] == cat}
            datasets.append({
                'label': cat,
                'data': [cat_data.get(y, 0) for y in years],
            })
    else:
        cursor.execute('''
            SELECT strftime('%Y', date) as year, COUNT(DISTINCT archer_id) as count
            FROM results
            GROUP BY year
            ORDER BY year
        ''')
        rows = [dict(r) for r in cursor.fetchall()]
        years = [r['year'] for r in rows]
        datasets = [{'label': 'Totalt', 'data': [r['count'] for r in rows]}]

    # Flag if historical sync is still incomplete (affects 2024/2025 counts)
    cursor.execute(
        'SELECT COUNT(*) FROM sync_status WHERE historical_synced=0 AND exists_online=1'
    )
    unsynced = cursor.fetchone()[0]
    conn.close()

    return {
        'years': years,
        'datasets': datasets,
        'historical_incomplete': unsynced > 0,
        'unsynced_count': unsynced,
    }


def get_categories() -> List[str]:
    """Get all unique categories."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT category FROM results ORDER BY category')
    categories = [row['category'] for row in cursor.fetchall()]
    conn.close()
    return categories


def get_distances() -> List[str]:
    """Get all unique distances."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT distance FROM results ORDER BY distance')
    distances = [row['distance'] for row in cursor.fetchall()]
    conn.close()
    return distances


def get_years() -> List[int]:
    """Get all years that have at least one result, newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT strftime('%Y', date) as year
        FROM results
        WHERE date IS NOT NULL
        ORDER BY year DESC
    ''')
    years = [int(row['year']) for row in cursor.fetchall()]
    conn.close()
    return years


def get_top_archers(
    n: int = 5,
    category: Optional[str] = None,
    distance: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the top N archers by best score, with optional filters."""
    n = max(2, min(10, n))
    conn = get_connection()
    cursor = conn.cursor()

    query = '''
        SELECT a.id, a.name, MAX(r.score) as best_score, COUNT(*) as competitions
        FROM results r
        JOIN archers a ON r.archer_id = a.id
        WHERE 1=1
    '''
    params: list = []
    if category:
        query += ' AND r.category = ?'
        params.append(category)
    if distance:
        query += ' AND r.distance = ?'
        params.append(distance)
    if date_from:
        query += ' AND r.date >= ?'
        params.append(date_from)
    if date_to:
        query += ' AND r.date <= ?'
        params.append(date_to)
    query += ' GROUP BY a.id, a.name ORDER BY best_score DESC LIMIT ?'
    params.append(n)

    cursor.execute(query, params)
    result = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return result


def get_archer_stats(archer_id: int) -> Dict[str, Any]:
    """Get statistics for an archer."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get general stats
    cursor.execute('''
        SELECT 
            COUNT(*) as total_competitions,
            AVG(score) as avg_score,
            MAX(score) as best_score,
            MIN(score) as worst_score,
            COUNT(CASE WHEN placement = 1 THEN 1 END) as first_places
        FROM results
        WHERE archer_id = ?
    ''', (archer_id,))
    
    stats = dict(cursor.fetchone())
    
    # Get stats by category
    cursor.execute('''
        SELECT 
            category,
            COUNT(*) as competitions,
            AVG(score) as avg_score,
            MAX(score) as best_score
        FROM results
        WHERE archer_id = ?
        GROUP BY category
    ''', (archer_id,))
    
    stats['by_category'] = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return stats


def get_archer_yearly_stats(
    archer_id: int,
    category: Optional[str] = None,
    distance: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Get per-year aggregate statistics for an archer.

    Returns a list sorted by year ascending, each entry containing:
    year, competitions, avg_score, best_score, worst_score.
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = '''
        SELECT
            strftime('%Y', date)  AS year,
            COUNT(*)              AS competitions,
            ROUND(AVG(score), 1)  AS avg_score,
            MAX(score)            AS best_score,
            MIN(score)            AS worst_score
        FROM results
        WHERE archer_id = ?
    '''
    params: list = [archer_id]

    if category:
        query += ' AND category = ?'
        params.append(category)
    if distance:
        query += ' AND distance = ?'
        params.append(distance)
    if date_from:
        query += ' AND date >= ?'
        params.append(date_from)
    if date_to:
        query += ' AND date <= ?'
        params.append(date_to)

    query += ' GROUP BY strftime(\'%Y\', date) ORDER BY year ASC'

    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_archer_name(archer_id: int) -> str:
    """Get a single archer's name (empty string if not found)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM archers WHERE id = ?', (archer_id,))
    row = cursor.fetchone()
    conn.close()
    return row['name'] if row else ''


def get_yearly_stats_bulk(
    archer_ids: List[int],
    category: Optional[str] = None,
    distance: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[int, Dict[str, Any]]:
    """
    Per-year aggregate statistics for many archers in one query.

    Returns {archer_id: {'name': ..., 'years': {year: row}}} where each row
    contains year, competitions, avg_score, best_score, worst_score.
    """
    if not archer_ids:
        return {}

    conn = get_connection()
    cursor = conn.cursor()

    placeholders = ','.join('?' * len(archer_ids))
    query = f'''
        SELECT
            r.archer_id,
            a.name                  AS archer_name,
            strftime('%Y', r.date)  AS year,
            COUNT(*)                AS competitions,
            ROUND(AVG(r.score), 1)  AS avg_score,
            MAX(r.score)            AS best_score,
            MIN(r.score)            AS worst_score
        FROM results r
        JOIN archers a ON a.id = r.archer_id
        WHERE r.archer_id IN ({placeholders})
    '''
    params: list = list(archer_ids)

    if category:
        query += ' AND r.category = ?'
        params.append(category)
    if distance:
        query += ' AND r.distance = ?'
        params.append(distance)
    if date_from:
        query += ' AND r.date >= ?'
        params.append(date_from)
    if date_to:
        query += ' AND r.date <= ?'
        params.append(date_to)

    query += " GROUP BY r.archer_id, strftime('%Y', r.date) ORDER BY r.archer_id, year"

    cursor.execute(query, params)
    stats: Dict[int, Dict[str, Any]] = {}
    for row in cursor.fetchall():
        r = dict(row)
        entry = stats.setdefault(r['archer_id'], {'name': r['archer_name'], 'years': {}})
        entry['years'][r['year']] = r
    conn.close()
    return stats


# ============== Class-level analysis ==============

def _per_archer_season_rows(
    category: Optional[str] = None,
    distance: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """One row per (archer, category, distance, year) with season aggregates."""
    conn = get_connection()
    cursor = conn.cursor()

    query = '''
        SELECT
            r.archer_id,
            a.name                  AS archer_name,
            a.club,
            r.category,
            r.distance,
            strftime('%Y', r.date)  AS year,
            COUNT(*)                AS competitions,
            ROUND(AVG(r.score), 1)  AS avg_score,
            MAX(r.score)            AS best_score
        FROM results r
        JOIN archers a ON a.id = r.archer_id
        WHERE 1=1
    '''
    params: list = []
    if category:
        query += ' AND r.category = ?'
        params.append(category)
    if distance:
        query += ' AND r.distance = ?'
        params.append(distance)
    if date_from:
        query += ' AND r.date >= ?'
        params.append(date_from)
    if date_to:
        query += ' AND r.date <= ?'
        params.append(date_to)
    query += " GROUP BY r.archer_id, r.category, r.distance, strftime('%Y', r.date)"

    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_class_report(
    category: Optional[str] = None,
    distance: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Level report per (category, distance, year): how many archers competed
    and how strong the class is (median/average of the archers' season
    averages, plus the best single score).

    Grouping always includes distance so scores from different formats
    (e.g. 18 m vs 3D) are never averaged together.
    """
    season_rows = _per_archer_season_rows(category, distance, date_from, date_to)

    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in season_rows:
        groups.setdefault((r['category'], r['distance'], r['year']), []).append(r)

    report = []
    for (cat, dist, year), rows in sorted(groups.items()):
        avgs = sorted(r['avg_score'] for r in rows)
        mid = len(avgs) // 2
        median = avgs[mid] if len(avgs) % 2 else round((avgs[mid - 1] + avgs[mid]) / 2, 1)
        report.append({
            'category': cat,
            'distance': dist,
            'year': year,
            'archers': len(rows),
            'results': sum(r['competitions'] for r in rows),
            'avg_score': round(sum(avgs) / len(avgs), 1),
            'median_score': median,
            'best_score': max(r['best_score'] for r in rows),
        })
    return report


def get_improving_archers(
    category: Optional[str] = None,
    distance: Optional[str] = None,
    min_results: int = 3,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Archers with the biggest season-over-season improvement in average score,
    compared within the same category and distance (so the change is not an
    artefact of switching class or format).

    Only archer-seasons with at least min_results competitions count, to
    filter out noise from one-off starts. The two most recent qualifying
    seasons are compared.
    """
    season_rows = _per_archer_season_rows(category, distance)

    by_archer: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in season_rows:
        if r['competitions'] >= min_results:
            by_archer.setdefault((r['archer_id'], r['category'], r['distance']), []).append(r)

    improvers = []
    for (archer_id, cat, dist), rows in by_archer.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: r['year'])
        prev, last = rows[-2], rows[-1]
        delta = round(last['avg_score'] - prev['avg_score'], 1)
        improvers.append({
            'archer_id': archer_id,
            'archer_name': last['archer_name'],
            'club': last['club'],
            'category': cat,
            'distance': dist,
            'prev_year': prev['year'],
            'prev_avg': prev['avg_score'],
            'prev_competitions': prev['competitions'],
            'last_year': last['year'],
            'last_avg': last['avg_score'],
            'last_competitions': last['competitions'],
            'improvement': delta,
            'improvement_pct': round(delta / prev['avg_score'] * 100, 1) if prev['avg_score'] else None,
        })

    improvers.sort(key=lambda r: r['improvement'], reverse=True)
    return improvers[:max(1, min(100, limit))]


# ============== Competition / Event browsing ==============

def get_events_list(
    category: Optional[str] = None,
    distance: Optional[str] = None,
    archer_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return all competition rounds (event × distance × category) that have results,
    with aggregated statistics.  Each row represents one round at one event.
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = '''
        SELECT
            e.id            AS event_db_id,
            e.event_id      AS external_event_id,
            e.name          AS event_name,
            e.event_url,
            r.date,
            r.distance,
            r.category,
            COUNT(DISTINCT r.archer_id)     AS participant_count,
            MAX(r.score)                    AS top_score,
            ROUND(AVG(r.score), 1)          AS avg_score,
            MIN(r.placement)                AS top_placement
        FROM events e
        JOIN results r ON r.event_id = e.id
        WHERE 1=1
    '''
    params: list = []

    if category:
        query += ' AND r.category = ?'
        params.append(category)
    if distance:
        query += ' AND r.distance = ?'
        params.append(distance)
    if archer_id:
        query += ' AND r.archer_id = ?'
        params.append(archer_id)
    if date_from:
        query += ' AND r.date >= ?'
        params.append(date_from)
    if date_to:
        query += ' AND r.date <= ?'
        params.append(date_to)

    query += ' GROUP BY e.id, r.distance, r.category ORDER BY r.date DESC'

    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_event_results(
    event_db_id: int,
    distance: Optional[str] = None,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return all archer results for a specific event (optionally filtered by
    distance and/or category), ordered by placement then score descending.
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = '''
        SELECT
            a.id        AS archer_id,
            a.name      AS archer_name,
            r.date,
            r.distance,
            r.category,
            r.score,
            r.placement
        FROM results r
        JOIN archers a ON a.id = r.archer_id
        WHERE r.event_id = ?
    '''
    params: list = [event_db_id]

    if distance:
        query += ' AND r.distance = ?'
        params.append(distance)
    if category:
        query += ' AND r.category = ?'
        params.append(category)

    query += ' ORDER BY r.placement ASC NULLS LAST, r.score DESC'

    cursor.execute(query, params)
    results = []
    for row in cursor.fetchall():
        r = dict(row)
        r['score_per_60'] = score_per_60(r['score'], r['distance'])
        results.append(r)
    conn.close()
    return results


# ============== Sync-related functions ==============

def add_archer_with_external_id(
    name: str, 
    external_id: int, 
    club: Optional[str] = None,
    club_id: Optional[int] = None,
    profile_url: Optional[str] = None
) -> int:
    """Add a new archer with external ID or get existing archer ID."""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # First check if archer exists by external_id
        cursor.execute('SELECT id FROM archers WHERE external_id = ?', (external_id,))
        row = cursor.fetchone()

        if row:
            archer_id = row['id']
            try:
                # Update info
                cursor.execute('''
                    UPDATE archers
                    SET name = ?, club = ?, club_id = ?, profile_url = ?, updated_at = ?
                    WHERE id = ?
                ''', (name, club, club_id, profile_url, datetime.now(), archer_id))
                conn.commit()
            except sqlite3.IntegrityError:
                # A different archer already has this exact name (a real name
                # collision between two people). Reassigning that archer's
                # name here would misattribute their results, so keep this
                # archer's existing name and only refresh the other fields.
                conn.rollback()
                cursor.execute('''
                    UPDATE archers
                    SET club = ?, club_id = ?, profile_url = ?, updated_at = ?
                    WHERE id = ?
                ''', (club, club_id, profile_url, datetime.now(), archer_id))
                conn.commit()
        else:
            # Try to insert new archer
            try:
                cursor.execute('''
                    INSERT INTO archers (name, external_id, club, club_id, profile_url)
                    VALUES (?, ?, ?, ?, ?)
                ''', (name, external_id, club, club_id, profile_url))
                archer_id = cursor.lastrowid
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                # Name already exists - update with external_id
                cursor.execute('SELECT id FROM archers WHERE name = ?', (name,))
                archer_id = cursor.fetchone()['id']
                cursor.execute('''
                    UPDATE archers
                    SET external_id = ?, club = ?, club_id = ?, profile_url = ?, updated_at = ?
                    WHERE id = ?
                ''', (external_id, club, club_id, profile_url, datetime.now(), archer_id))
                conn.commit()

        return archer_id
    finally:
        conn.close()


def get_archer_by_external_id(external_id: int) -> Optional[Dict[str, Any]]:
    """Get archer by external ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM archers WHERE external_id = ?', (external_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_sync_status(
    external_id: int,
    archer_id: Optional[int] = None,
    name: Optional[str] = None,
    club: Optional[str] = None,
    club_id: Optional[int] = None,
    exists_online: bool = True,
    historical_synced: bool = False,
    last_result_date: Optional[str] = None,
    total_results: int = 0,
    years_available: Optional[str] = None
) -> None:
    """Insert or update sync status for an archer."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO sync_status 
            (external_id, archer_id, name, club, club_id, exists_online, 
             historical_synced, last_sync, last_result_date, total_results, years_available)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(external_id) DO UPDATE SET
            archer_id = COALESCE(excluded.archer_id, sync_status.archer_id),
            name = COALESCE(excluded.name, sync_status.name),
            club = COALESCE(excluded.club, sync_status.club),
            club_id = COALESCE(excluded.club_id, sync_status.club_id),
            exists_online = excluded.exists_online,
            historical_synced = CASE 
                WHEN excluded.historical_synced = 1 THEN 1 
                ELSE sync_status.historical_synced 
            END,
            last_sync = excluded.last_sync,
            last_result_date = COALESCE(excluded.last_result_date, sync_status.last_result_date),
            total_results = CASE 
                WHEN excluded.total_results > 0 THEN excluded.total_results 
                ELSE sync_status.total_results 
            END,
            years_available = COALESCE(excluded.years_available, sync_status.years_available)
    ''', (external_id, archer_id, name, club, club_id, exists_online, 
          historical_synced, datetime.now(), last_result_date, total_results, years_available))
    
    conn.commit()
    conn.close()


def get_sync_status(external_id: int) -> Optional[Dict[str, Any]]:
    """Get sync status for an archer."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM sync_status WHERE external_id = ?', (external_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_sync_status(
    only_existing: bool = True, 
    only_unsynced_historical: bool = False,
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Get all sync statuses with optional filtering."""
    conn = get_connection()
    cursor = conn.cursor()
    
    query = 'SELECT * FROM sync_status WHERE 1=1'
    params = []
    
    if only_existing:
        query += ' AND exists_online = 1'
    
    if only_unsynced_historical:
        query += ' AND historical_synced = 0'
    
    query += ' ORDER BY external_id'
    
    if limit:
        query += ' LIMIT ?'
        params.append(limit)
    
    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_max_external_id() -> int:
    """Get the highest external ID we know about."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT MAX(external_id) as max_id FROM sync_status')
    row = cursor.fetchone()
    conn.close()
    return row['max_id'] if row and row['max_id'] else 0


def mark_archer_not_found(external_id: int) -> None:
    """Mark an external ID as not existing (404)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO sync_status (external_id, exists_online, last_sync)
        VALUES (?, 0, ?)
        ON CONFLICT(external_id) DO UPDATE SET
            exists_online = 0,
            last_sync = excluded.last_sync
    ''', (external_id, datetime.now()))
    conn.commit()
    conn.close()


def log_sync_error(
    external_id: int,
    error_type: str,
    error_message: str,
    http_status: Optional[int] = None,
    request_url: Optional[str] = None,
    response_body: Optional[str] = None
) -> int:
    """Log a sync error to the outbox."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Truncate response body if too long
    if response_body and len(response_body) > 10000:
        response_body = response_body[:10000] + '... [truncated]'
    
    cursor.execute('''
        INSERT INTO sync_errors 
            (external_id, error_type, error_message, http_status, request_url, response_body)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (external_id, error_type, error_message, http_status, request_url, response_body))
    
    error_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return error_id


def get_unresolved_errors(limit: int = 100) -> List[Dict[str, Any]]:
    """Get unresolved sync errors."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM sync_errors 
        WHERE resolved = 0 
        ORDER BY created_at DESC 
        LIMIT ?
    ''', (limit,))
    errors = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return errors


def resolve_error(error_id: int) -> None:
    """Mark an error as resolved."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE sync_errors 
        SET resolved = 1, resolved_at = ?
        WHERE id = ?
    ''', (datetime.now(), error_id))
    conn.commit()
    conn.close()


def start_sync_log(job_type: str, total: Optional[int] = None) -> int:
    """Start a new sync log entry."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO sync_log (job_type, started_at, status, total)
        VALUES (?, ?, 'running', ?)
    ''', (job_type, datetime.now(), total))
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return log_id


def update_sync_log(
    log_id: int,
    archers_processed: int = 0,
    results_added: int = 0,
    errors_count: int = 0,
    status: str = 'running',
    details: Optional[str] = None
) -> None:
    """Update a sync log entry."""
    conn = get_connection()
    cursor = conn.cursor()

    completed_at = datetime.now() if status in ('completed', 'failed', 'stopped') else None

    cursor.execute('''
        UPDATE sync_log
        SET archers_processed = ?, results_added = ?, errors_count = ?,
            status = ?, completed_at = ?, details = ?
        WHERE id = ?
    ''', (archers_processed, results_added, errors_count, status, completed_at, details, log_id))

    conn.commit()
    conn.close()


def get_recent_sync_logs(limit: int = 20) -> List[Dict[str, Any]]:
    """Get recent sync log entries."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM sync_log
        ORDER BY started_at DESC
        LIMIT ?
    ''', (limit,))
    logs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return logs


def get_current_sync_log() -> Optional[Dict[str, Any]]:
    """Get the most recent sync_log entry, whatever its status."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM sync_log ORDER BY started_at DESC LIMIT 1')
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_sync_stats() -> Dict[str, Any]:
    """Get overall sync statistics."""
    conn = get_connection()
    cursor = conn.cursor()
    
    stats = {}
    
    # Total archers found
    cursor.execute('SELECT COUNT(*) as count FROM sync_status WHERE exists_online = 1')
    stats['total_archers_found'] = cursor.fetchone()['count']
    
    # Total archers not found
    cursor.execute('SELECT COUNT(*) as count FROM sync_status WHERE exists_online = 0')
    stats['total_not_found'] = cursor.fetchone()['count']
    
    # Historical synced
    cursor.execute('SELECT COUNT(*) as count FROM sync_status WHERE historical_synced = 1')
    stats['historical_synced'] = cursor.fetchone()['count']
    
    # Total results
    cursor.execute('SELECT COUNT(*) as count FROM results')
    stats['total_results'] = cursor.fetchone()['count']
    
    # Unresolved errors
    cursor.execute('SELECT COUNT(*) as count FROM sync_errors WHERE resolved = 0')
    stats['unresolved_errors'] = cursor.fetchone()['count']
    
    # Last sync time
    cursor.execute('SELECT MAX(last_sync) as last_sync FROM sync_status')
    row = cursor.fetchone()
    stats['last_sync'] = row['last_sync'] if row else None
    
    # Max external ID
    cursor.execute('SELECT MAX(external_id) as max_id FROM sync_status')
    row = cursor.fetchone()
    stats['max_external_id'] = row['max_id'] if row and row['max_id'] else 0
    
    conn.close()
    return stats


if __name__ == '__main__':
    init_database()


# ---------------------------------------------------------------------------
# PostgreSQL override
# When DATABASE_URL is set, replace all SQLite functions with the Postgres
# equivalents. This must be at the bottom so the postgres functions shadow
# the sqlite ones defined above.
# ---------------------------------------------------------------------------
if os.environ.get('DATABASE_URL'):
    try:
        from src.db_postgres import (  # noqa: F401, F811
            init_database, get_connection,
            add_archer, add_event, add_result,
            get_all_archers, get_archer_results, get_all_results,
            get_categories, get_distances,
            get_archer_stats, get_archer_yearly_stats,
            add_archer_with_external_id, get_archer_by_external_id,
            upsert_sync_status, get_sync_status, get_all_sync_status,
            get_max_external_id, mark_archer_not_found,
            log_sync_error, get_unresolved_errors, resolve_error,
            start_sync_log, update_sync_log, get_recent_sync_logs,
            get_sync_stats,
            get_top_archers,
        )
    except ImportError as _e:
        import warnings
        warnings.warn(f"DATABASE_URL set but psycopg2 not available ({_e}). Falling back to SQLite.")
