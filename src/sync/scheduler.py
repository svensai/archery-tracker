"""
Scheduler module for running sync jobs on a schedule.
Uses APScheduler for job scheduling.
"""

import logging
import signal
import sys
import time
from typing import Optional
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from .config import SyncConfig, get_config
from .worker import SyncWorker, setup_logging

logger = logging.getLogger(__name__)


class SyncScheduler:
    """
    Scheduler for running sync jobs at configured intervals.
    """
    
    def __init__(self, config: Optional[SyncConfig] = None):
        self.config = config or get_config()
        self.scheduler = BackgroundScheduler()
        self.worker: Optional[SyncWorker] = None
        self._running = False
        
        # Set up scheduler event listeners
        self.scheduler.add_listener(self._job_executed, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(self._job_error, EVENT_JOB_ERROR)
    
    def _job_executed(self, event):
        """Called when a job completes successfully."""
        logger.info(f"Job {event.job_id} executed successfully at {datetime.now()}")
    
    def _job_error(self, event):
        """Called when a job encounters an error."""
        logger.error(f"Job {event.job_id} failed with exception: {event.exception}")
    
    def _create_worker(self) -> SyncWorker:
        """Create a new worker instance."""
        return SyncWorker(self.config)
    
    def _run_daily_sync(self):
        """Job function for daily sync."""
        logger.info("Running scheduled daily sync")
        try:
            with self._create_worker() as worker:
                worker.run_daily_sync()
        except Exception as e:
            logger.error(f"Daily sync job failed: {e}")
    
    def _run_discovery(self):
        """Job function for discovering new archers."""
        logger.info("Running scheduled discovery")
        try:
            with self._create_worker() as worker:
                stats = worker.discover_new_archers()
                logger.info(f"Discovery completed: {stats}")
        except Exception as e:
            logger.error(f"Discovery job failed: {e}")
    
    def _run_historical_sync(self):
        """Job function for syncing historical data."""
        logger.info("Running scheduled historical sync")
        try:
            with self._create_worker() as worker:
                stats = worker.sync_historical()
                logger.info(f"Historical sync completed: {stats}")
        except Exception as e:
            logger.error(f"Historical sync job failed: {e}")
    
    def add_daily_sync_job(self, hour: Optional[int] = None, minute: Optional[int] = None):
        """
        Add the daily sync job to the scheduler.
        
        Args:
            hour: Hour to run (defaults to config.sync_hour)
            minute: Minute to run (defaults to config.sync_minute)
        """
        hour = hour if hour is not None else self.config.sync_hour
        minute = minute if minute is not None else self.config.sync_minute
        
        self.scheduler.add_job(
            self._run_daily_sync,
            CronTrigger(hour=hour, minute=minute),
            id='daily_sync',
            name='Daily Sync',
            replace_existing=True
        )
        logger.info(f"Added daily sync job scheduled for {hour:02d}:{minute:02d}")
    
    def add_discovery_job(self, hour: int = 2, minute: int = 0):
        """
        Add a discovery job to find new archers.
        
        Args:
            hour: Hour to run
            minute: Minute to run
        """
        self.scheduler.add_job(
            self._run_discovery,
            CronTrigger(hour=hour, minute=minute),
            id='discovery',
            name='Discover New Archers',
            replace_existing=True
        )
        logger.info(f"Added discovery job scheduled for {hour:02d}:{minute:02d}")
    
    def add_historical_sync_job(self, hour: int = 4, minute: int = 0):
        """
        Add a historical sync job.
        
        Args:
            hour: Hour to run
            minute: Minute to run
        """
        self.scheduler.add_job(
            self._run_historical_sync,
            CronTrigger(hour=hour, minute=minute),
            id='historical_sync',
            name='Historical Sync',
            replace_existing=True
        )
        logger.info(f"Added historical sync job scheduled for {hour:02d}:{minute:02d}")
    
    def run_job_now(self, job_id: str):
        """
        Run a specific job immediately.
        
        Args:
            job_id: The job ID ('daily_sync', 'discovery', 'historical_sync')
        """
        job = self.scheduler.get_job(job_id)
        if job:
            logger.info(f"Running job {job_id} immediately")
            job.func()
        else:
            logger.warning(f"Job {job_id} not found")
    
    def get_jobs(self):
        """Get list of scheduled jobs."""
        return [
            {
                'id': job.id,
                'name': job.name,
                'next_run_time': str(job.next_run_time) if job.next_run_time else None,
                'trigger': str(job.trigger)
            }
            for job in self.scheduler.get_jobs()
        ]
    
    def start(self, run_daily_sync_now: bool = False):
        """
        Start the scheduler.
        
        Args:
            run_daily_sync_now: If True, run the daily sync immediately on startup
        """
        if self._running:
            logger.warning("Scheduler is already running")
            return
        
        logger.info("Starting scheduler")
        self.scheduler.start()
        self._running = True
        
        if run_daily_sync_now:
            logger.info("Running initial daily sync")
            self._run_daily_sync()
    
    def stop(self):
        """Stop the scheduler."""
        if not self._running:
            return
        
        logger.info("Stopping scheduler")
        self.scheduler.shutdown(wait=True)
        self._running = False
    
    def run_forever(self):
        """
        Run the scheduler indefinitely.
        Handles shutdown signals gracefully.
        """
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            self.stop()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        self.start()
        
        logger.info("Scheduler running. Press Ctrl+C to stop.")
        try:
            while self._running:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            self.stop()


def create_default_scheduler(config: Optional[SyncConfig] = None) -> SyncScheduler:
    """
    Create a scheduler with default job configuration.
    
    - Daily sync at 3:00 AM
    - Discovery at 2:00 AM  
    - Historical sync: not scheduled by default (run manually via run_sync.py --historical)
    """
    scheduler = SyncScheduler(config)
    
    # Daily sync every day at 3 AM
    scheduler.add_daily_sync_job(hour=3, minute=0)
    
    # Discovery every day at 2 AM (before daily sync)
    scheduler.add_discovery_job(hour=2, minute=0)
    
    return scheduler


if __name__ == '__main__':
    import argparse
    from src.database import init_database
    
    parser = argparse.ArgumentParser(description='Archery Sync Scheduler')
    parser.add_argument('--now', action='store_true', help='Run daily sync immediately on startup')
    parser.add_argument('--log-level', default='INFO', help='Log level (DEBUG, INFO, WARNING, ERROR)')
    args = parser.parse_args()
    
    # Initialize
    init_database()
    setup_logging(args.log_level)
    
    # Create and start scheduler
    scheduler = create_default_scheduler()
    
    # Show scheduled jobs
    logger.info("Scheduled jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job['name']}: {job['trigger']}, next run: {job['next_run_time']}")
    
    # Run forever
    scheduler.run_forever()
