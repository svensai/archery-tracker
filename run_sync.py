"""
Entry point for the sync worker.
Run from the project root: python run_sync.py [--discover | --daily | --historical | --daemon]
"""

import argparse
import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from src.database import init_database
from src.sync.worker import SyncWorker, setup_logging
from src.sync.scheduler import create_default_scheduler


def main():
    parser = argparse.ArgumentParser(description='Archery Sync Worker')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--discover', action='store_true',
                       help='Scan all archer IDs and add new archers to the database')
    group.add_argument('--daily', action='store_true',
                       help='Run one-off daily sync (discover new + sync current season)')
    group.add_argument('--historical', action='store_true',
                       help='Sync historical results for all known archers')
    group.add_argument('--daemon', action='store_true',
                       help='Run as background scheduler (daily at 03:00, discovery at 02:00)')

    parser.add_argument('--years', nargs='+', type=int, metavar='YEAR',
                        help='Years to fetch for --historical (e.g. --years 2024 2025)')
    parser.add_argument('--force', action='store_true',
                        help='Force re-sync even if archer is already marked historical_synced')
    parser.add_argument('--start-id', type=int, default=1,
                        help='Starting archer ID for --discover (default: 1)')
    parser.add_argument('--end-id', type=int,
                        help='Ending archer ID for --discover (default: config max)')
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='Log level (default: INFO)')
    parser.add_argument('--log-file', default=None,
                        help='Optional log file path (e.g. logs/sync.log)')

    args = parser.parse_args()

    # Set up logging
    setup_logging(args.log_level, args.log_file)

    # Ensure database tables exist
    init_database()

    if args.daemon:
        scheduler = create_default_scheduler()
        print("Starting scheduler daemon. Press Ctrl+C to stop.")
        scheduler.run_forever()

    else:
        with SyncWorker() as worker:
            if args.discover:
                stats = worker.discover_archers(
                    start_id=args.start_id,
                    end_id=args.end_id
                )
                print(f"Discovery complete: {stats}")

            elif args.historical:
                stats = worker.sync_historical(years=args.years, force=args.force)
                print(f"Historical sync complete: {stats}")

            else:
                # Default: --daily (or no flag)
                worker.run_daily_sync()
                print("Daily sync complete.")


if __name__ == '__main__':
    main()
