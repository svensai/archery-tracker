"""
Migration script: copy all data from SQLite to PostgreSQL.

Usage:
    export DATABASE_URL=postgresql://user:password@localhost:5432/archery
    python -m src.migrate_to_postgres
"""

import os
import sys
import sqlite3

# Ensure src is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

SQLITE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'archery.db')


def migrate():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is not set.")
        print("Example: export DATABASE_URL=postgresql://user:password@localhost:5432/archery")
        sys.exit(1)

    import psycopg2
    from src.db_postgres import init_database

    print(f"Source SQLite:     {SQLITE_PATH}")
    print(f"Target PostgreSQL: {database_url.split('@')[-1]}")
    print()

    # Initialize PostgreSQL schema
    print("Initializing PostgreSQL schema...")
    init_database()

    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row
    dst = psycopg2.connect(database_url)

    tables = [
        ('archers',     'id, name, external_id, club, club_id, profile_url, created_at, updated_at'),
        ('events',      'id, event_id, name, event_url, created_at'),
        ('results',     'id, archer_id, event_id, date, distance, category, score, placement, created_at'),
        ('sync_status', 'external_id, archer_id, name, club, club_id, exists_online, historical_synced, last_sync, last_result_date, total_results, years_available, created_at'),
        ('sync_errors', 'id, external_id, error_type, error_message, http_status, request_url, response_body, retry_count, resolved, created_at, resolved_at'),
        ('sync_log',    'id, job_type, started_at, completed_at, archers_processed, results_added, errors_count, status, details'),
    ]

    for table, columns in tables:
        src_cur = src.cursor()
        try:
            src_cur.execute(f'SELECT {columns} FROM {table}')
        except sqlite3.OperationalError as e:
            print(f"  Skipping {table}: {e}")
            continue

        rows = src_cur.fetchall()
        if not rows:
            print(f"  {table}: 0 rows (skipping)")
            continue

        col_list = [c.strip() for c in columns.split(',')]
        placeholders = ', '.join(['%s'] * len(col_list))
        col_names = ', '.join(col_list)

        dst_cur = dst.cursor()
        inserted = 0
        skipped = 0
        for row in rows:
            try:
                dst_cur.execute(
                    f'INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING',
                    tuple(row)
                )
                if dst_cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"    Warning: row in {table} failed: {e}")
                skipped += 1

        dst.commit()
        print(f"  {table}: {inserted} inserted, {skipped} skipped")

    # Reset sequences so new inserts get correct IDs
    print("\nResetting PostgreSQL sequences...")
    dst_cur = dst.cursor()
    for table, id_col in [('archers', 'id'), ('events', 'id'), ('results', 'id'),
                           ('sync_errors', 'id'), ('sync_log', 'id')]:
        try:
            dst_cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{id_col}'), COALESCE(MAX({id_col}), 1)) FROM {table}")
            print(f"  Reset sequence for {table}.{id_col}")
        except Exception as e:
            print(f"  Could not reset sequence for {table}: {e}")

    dst.commit()
    src.close()
    dst.close()
    print("\nMigration complete.")


if __name__ == '__main__':
    migrate()
