"""
PostgreSQL implementation of all database functions.
Activated automatically when the DATABASE_URL environment variable is set.

Usage:
    export DATABASE_URL=postgresql://user:password@localhost:5432/archery
    python -m src.app
"""

import os
from datetime import datetime
from typing import Optional, List, Dict, Any

import psycopg2
import psycopg2.pool
import psycopg2.extras

_DATABASE_URL = os.environ.get('DATABASE_URL', '')
_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(1, 10, _DATABASE_URL)
    return _pool


def get_connection():
    """Get a PostgreSQL connection from the pool."""
    return _get_pool().getconn()


def _release(conn):
    _get_pool().putconn(conn)


def _rows_to_dicts(cursor) -> List[Dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _row_to_dict(cursor) -> Optional[Dict]:
    cols = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    return dict(zip(cols, row)) if row else None


def init_database():
    """Initialize PostgreSQL database with required tables."""
    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute('''
            CREATE TABLE IF NOT EXISTS archers (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                external_id INTEGER,
                club TEXT,
                club_id INTEGER,
                profile_url TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                event_url TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS results (
                id SERIAL PRIMARY KEY,
                archer_id INTEGER NOT NULL REFERENCES archers(id),
                event_id INTEGER NOT NULL REFERENCES events(id),
                date TEXT NOT NULL,
                distance TEXT NOT NULL,
                category TEXT NOT NULL,
                score INTEGER NOT NULL,
                placement INTEGER,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(archer_id, event_id, distance, category)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS sync_status (
                external_id INTEGER PRIMARY KEY,
                archer_id INTEGER REFERENCES archers(id),
                name TEXT,
                club TEXT,
                club_id INTEGER,
                exists_online BOOLEAN DEFAULT TRUE,
                historical_synced BOOLEAN DEFAULT FALSE,
                last_sync TIMESTAMP,
                last_result_date TEXT,
                total_results INTEGER DEFAULT 0,
                years_available TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS sync_errors (
                id SERIAL PRIMARY KEY,
                external_id INTEGER,
                error_type TEXT,
                error_message TEXT,
                http_status INTEGER,
                request_url TEXT,
                response_body TEXT,
                retry_count INTEGER DEFAULT 0,
                resolved BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW(),
                resolved_at TIMESTAMP
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS sync_log (
                id SERIAL PRIMARY KEY,
                job_type TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                archers_processed INTEGER DEFAULT 0,
                results_added INTEGER DEFAULT 0,
                errors_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                details TEXT
            )
        ''')

        # Indexes
        for idx_sql in [
            'CREATE INDEX IF NOT EXISTS idx_results_archer ON results(archer_id)',
            'CREATE INDEX IF NOT EXISTS idx_results_date ON results(date)',
            'CREATE INDEX IF NOT EXISTS idx_results_category ON results(category)',
            'CREATE INDEX IF NOT EXISTS idx_archers_external_id ON archers(external_id)',
            'CREATE INDEX IF NOT EXISTS idx_sync_status_last_sync ON sync_status(last_sync)',
            'CREATE INDEX IF NOT EXISTS idx_sync_errors_resolved ON sync_errors(resolved)',
        ]:
            cur.execute(idx_sql)

        conn.commit()
        print(f"PostgreSQL database initialized: {_DATABASE_URL.split('@')[-1]}")
    finally:
        _release(conn)


def add_archer(name: str, profile_url: Optional[str] = None,
               external_id: Optional[int] = None, club: Optional[str] = None,
               club_id: Optional[int] = None) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO archers (name, profile_url, external_id, club, club_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE
                SET profile_url = COALESCE(EXCLUDED.profile_url, archers.profile_url),
                    updated_at = NOW()
            RETURNING id
        ''', (name, profile_url, external_id, club, club_id))
        archer_id = cur.fetchone()[0]
        conn.commit()
        return archer_id
    finally:
        _release(conn)


def add_event(event_id: str, name: str, event_url: Optional[str] = None) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO events (event_id, name, event_url)
            VALUES (%s, %s, %s)
            ON CONFLICT (event_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        ''', (event_id, name, event_url))
        db_id = cur.fetchone()[0]
        conn.commit()
        return db_id
    finally:
        _release(conn)


def add_result(archer_id: int, event_id: int, date: str, distance: str,
               category: str, score: int, placement: Optional[int] = None) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO results (archer_id, event_id, date, distance, category, score, placement)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (archer_id, event_id, distance, category) DO UPDATE
                SET score = EXCLUDED.score,
                    placement = EXCLUDED.placement,
                    date = EXCLUDED.date
            RETURNING id
        ''', (archer_id, event_id, date, distance, category, score, placement))
        result_id = cur.fetchone()[0]
        conn.commit()
        return result_id
    finally:
        _release(conn)


def get_all_archers() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT * FROM archers ORDER BY name')
        return _rows_to_dicts(cur)
    finally:
        _release(conn)


def get_archer_results(archer_id: int, category: Optional[str] = None,
                       distance: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        query = '''
            SELECT r.*, e.name as event_name, e.event_id as external_event_id, a.name as archer_name
            FROM results r
            JOIN events e ON r.event_id = e.id
            JOIN archers a ON r.archer_id = a.id
            WHERE r.archer_id = %s
        '''
        params = [archer_id]
        if category:
            query += ' AND r.category = %s'
            params.append(category)
        if distance:
            query += ' AND r.distance = %s'
            params.append(distance)
        query += ' ORDER BY r.date DESC'
        cur.execute(query, params)
        return _rows_to_dicts(cur)
    finally:
        _release(conn)


def get_all_results(category: Optional[str] = None,
                    distance: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        query = '''
            SELECT r.*, e.name as event_name, e.event_id as external_event_id, a.name as archer_name
            FROM results r
            JOIN events e ON r.event_id = e.id
            JOIN archers a ON r.archer_id = a.id
            WHERE 1=1
        '''
        params = []
        if category:
            query += ' AND r.category = %s'
            params.append(category)
        if distance:
            query += ' AND r.distance = %s'
            params.append(distance)
        query += ' ORDER BY r.date DESC'
        cur.execute(query, params)
        return _rows_to_dicts(cur)
    finally:
        _release(conn)


def get_categories() -> List[str]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT category FROM results ORDER BY category')
        return [row[0] for row in cur.fetchall()]
    finally:
        _release(conn)


def get_distances() -> List[str]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT distance FROM results ORDER BY distance')
        return [row[0] for row in cur.fetchall()]
    finally:
        _release(conn)


def get_archer_stats(archer_id: int) -> Dict[str, Any]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT
                COUNT(*) as total_competitions,
                AVG(score) as avg_score,
                MAX(score) as best_score,
                MIN(score) as worst_score,
                COUNT(CASE WHEN placement = 1 THEN 1 END) as first_places
            FROM results
            WHERE archer_id = %s
        ''', (archer_id,))
        stats = _row_to_dict(cur) or {}

        cur.execute('''
            SELECT category, COUNT(*) as competitions, AVG(score) as avg_score, MAX(score) as best_score
            FROM results
            WHERE archer_id = %s
            GROUP BY category
        ''', (archer_id,))
        stats['by_category'] = _rows_to_dicts(cur)
        return stats
    finally:
        _release(conn)


def get_archer_yearly_stats(archer_id: int, category: Optional[str] = None,
                             distance: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        query = '''
            SELECT
                EXTRACT(YEAR FROM date::date)::int AS year,
                COUNT(*) AS competitions,
                ROUND(AVG(score)::numeric, 1) AS avg_score,
                MAX(score) AS best_score,
                MIN(score) AS worst_score
            FROM results
            WHERE archer_id = %s
        '''
        params: list = [archer_id]
        if category:
            query += ' AND category = %s'
            params.append(category)
        if distance:
            query += ' AND distance = %s'
            params.append(distance)
        query += ' GROUP BY EXTRACT(YEAR FROM date::date) ORDER BY year ASC'
        cur.execute(query, params)
        return _rows_to_dicts(cur)
    finally:
        _release(conn)


def add_archer_with_external_id(name: str, external_id: int, club: Optional[str] = None,
                                  club_id: Optional[int] = None,
                                  profile_url: Optional[str] = None) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO archers (name, external_id, club, club_id, profile_url)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE
                SET external_id = COALESCE(EXCLUDED.external_id, archers.external_id),
                    club = COALESCE(EXCLUDED.club, archers.club),
                    club_id = COALESCE(EXCLUDED.club_id, archers.club_id),
                    profile_url = COALESCE(EXCLUDED.profile_url, archers.profile_url),
                    updated_at = NOW()
            RETURNING id
        ''', (name, external_id, club, club_id, profile_url))
        archer_id = cur.fetchone()[0]
        conn.commit()
        return archer_id
    finally:
        _release(conn)


def get_archer_by_external_id(external_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT * FROM archers WHERE external_id = %s', (external_id,))
        return _row_to_dict(cur)
    finally:
        _release(conn)


def upsert_sync_status(external_id: int, archer_id: Optional[int] = None,
                        name: Optional[str] = None, club: Optional[str] = None,
                        club_id: Optional[int] = None, exists_online: bool = True,
                        historical_synced: bool = False,
                        last_result_date: Optional[str] = None,
                        total_results: int = 0,
                        years_available: Optional[str] = None) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO sync_status
                (external_id, archer_id, name, club, club_id, exists_online,
                 historical_synced, last_sync, last_result_date, total_results, years_available)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (external_id) DO UPDATE SET
                archer_id = COALESCE(EXCLUDED.archer_id, sync_status.archer_id),
                name = COALESCE(EXCLUDED.name, sync_status.name),
                club = COALESCE(EXCLUDED.club, sync_status.club),
                club_id = COALESCE(EXCLUDED.club_id, sync_status.club_id),
                exists_online = EXCLUDED.exists_online,
                historical_synced = CASE WHEN EXCLUDED.historical_synced THEN TRUE ELSE sync_status.historical_synced END,
                last_sync = EXCLUDED.last_sync,
                last_result_date = COALESCE(EXCLUDED.last_result_date, sync_status.last_result_date),
                total_results = CASE WHEN EXCLUDED.total_results > 0 THEN EXCLUDED.total_results ELSE sync_status.total_results END,
                years_available = COALESCE(EXCLUDED.years_available, sync_status.years_available)
        ''', (external_id, archer_id, name, club, club_id, exists_online,
              historical_synced, datetime.now(), last_result_date, total_results, years_available))
        conn.commit()
    finally:
        _release(conn)


def get_sync_status(external_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT * FROM sync_status WHERE external_id = %s', (external_id,))
        return _row_to_dict(cur)
    finally:
        _release(conn)


def get_all_sync_status(only_existing: bool = True, only_unsynced_historical: bool = False,
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        query = 'SELECT * FROM sync_status WHERE 1=1'
        params = []
        if only_existing:
            query += ' AND exists_online = TRUE'
        if only_unsynced_historical:
            query += ' AND historical_synced = FALSE'
        query += ' ORDER BY external_id'
        if limit:
            query += ' LIMIT %s'
            params.append(limit)
        cur.execute(query, params)
        return _rows_to_dicts(cur)
    finally:
        _release(conn)


def get_max_external_id() -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT MAX(external_id) FROM sync_status')
        row = cur.fetchone()
        return row[0] if row and row[0] else 0
    finally:
        _release(conn)


def mark_archer_not_found(external_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO sync_status (external_id, exists_online, last_sync)
            VALUES (%s, FALSE, %s)
            ON CONFLICT (external_id) DO UPDATE SET
                exists_online = FALSE,
                last_sync = EXCLUDED.last_sync
        ''', (external_id, datetime.now()))
        conn.commit()
    finally:
        _release(conn)


def log_sync_error(external_id: int, error_type: str, error_message: str,
                    http_status: Optional[int] = None, request_url: Optional[str] = None,
                    response_body: Optional[str] = None) -> int:
    conn = get_connection()
    try:
        if response_body and len(response_body) > 10000:
            response_body = response_body[:10000] + '... [truncated]'
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO sync_errors
                (external_id, error_type, error_message, http_status, request_url, response_body)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (external_id, error_type, error_message, http_status, request_url, response_body))
        error_id = cur.fetchone()[0]
        conn.commit()
        return error_id
    finally:
        _release(conn)


def get_unresolved_errors(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT * FROM sync_errors WHERE resolved = FALSE
            ORDER BY created_at DESC LIMIT %s
        ''', (limit,))
        return _rows_to_dicts(cur)
    finally:
        _release(conn)


def resolve_error(error_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            UPDATE sync_errors SET resolved = TRUE, resolved_at = %s WHERE id = %s
        ''', (datetime.now(), error_id))
        conn.commit()
    finally:
        _release(conn)


def start_sync_log(job_type: str) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO sync_log (job_type, started_at, status) VALUES (%s, %s, 'running')
            RETURNING id
        ''', (job_type, datetime.now()))
        log_id = cur.fetchone()[0]
        conn.commit()
        return log_id
    finally:
        _release(conn)


def update_sync_log(log_id: int, archers_processed: int = 0, results_added: int = 0,
                     errors_count: int = 0, status: str = 'running',
                     details: Optional[str] = None) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        completed_at = datetime.now() if status in ('completed', 'failed') else None
        cur.execute('''
            UPDATE sync_log
            SET archers_processed = %s, results_added = %s, errors_count = %s,
                status = %s, completed_at = %s, details = %s
            WHERE id = %s
        ''', (archers_processed, results_added, errors_count, status, completed_at, details, log_id))
        conn.commit()
    finally:
        _release(conn)


def get_recent_sync_logs(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT * FROM sync_log ORDER BY started_at DESC LIMIT %s', (limit,))
        return _rows_to_dicts(cur)
    finally:
        _release(conn)


def get_sync_stats() -> Dict[str, Any]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        stats = {}
        cur.execute('SELECT COUNT(*) FROM sync_status WHERE exists_online = TRUE')
        stats['total_archers_found'] = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM sync_status WHERE exists_online = FALSE')
        stats['total_not_found'] = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM sync_status WHERE historical_synced = TRUE')
        stats['historical_synced'] = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM results')
        stats['total_results'] = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM sync_errors WHERE resolved = FALSE')
        stats['unresolved_errors'] = cur.fetchone()[0]
        cur.execute('SELECT MAX(last_sync) FROM sync_status')
        row = cur.fetchone()
        stats['last_sync'] = row[0].isoformat() if row and row[0] else None
        cur.execute('SELECT MAX(external_id) FROM sync_status')
        row = cur.fetchone()
        stats['max_external_id'] = row[0] if row and row[0] else 0
        return stats
    finally:
        _release(conn)
