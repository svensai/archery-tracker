"""
Sync worker module - handles discovery, daily sync, and historical import.
"""

import logging
import time
import signal
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime
from threading import Event

from .config import SyncConfig, get_config
from .fetcher import ArcherFetcher, FetchResult
from ..database import (
    init_database, add_archer_with_external_id, add_event, add_result,
    upsert_sync_status, get_sync_status, get_all_sync_status,
    get_max_external_id, mark_archer_not_found, log_sync_error,
    start_sync_log, update_sync_log, get_sync_stats
)
from ..scraper import (
    parse_archer_page, parse_result_html, check_archer_exists,
    get_latest_result_date, ArcherInfo
)

# Set up logging
logger = logging.getLogger(__name__)


class SyncWorker:
    """
    Main sync worker that coordinates all sync operations.
    """
    
    def __init__(self, config: Optional[SyncConfig] = None):
        self.config = config or get_config()
        self.fetcher = ArcherFetcher(self.config)
        self._stop_event = Event()
        self._current_log_id: Optional[int] = None
        
        # Stats for current job
        self._archers_processed = 0
        self._results_added = 0
        self._errors_count = 0
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.stop()
    
    def stop(self):
        """Stop the worker gracefully."""
        self._stop_event.set()
    
    def is_stopped(self) -> bool:
        """Check if worker should stop."""
        return self._stop_event.is_set()
    
    def _reset_stats(self):
        """Reset job statistics."""
        self._archers_processed = 0
        self._results_added = 0
        self._errors_count = 0
    
    def _update_log(self, status: str = 'running', details: Optional[str] = None):
        """Update the current sync log."""
        if self._current_log_id:
            update_sync_log(
                self._current_log_id,
                archers_processed=self._archers_processed,
                results_added=self._results_added,
                errors_count=self._errors_count,
                status=status,
                details=details
            )
    
    # ==================== Discovery ====================
    
    def discover_archers(
        self, 
        start_id: int = 1, 
        end_id: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Dict[str, Any]:
        """
        Discover all archers by scanning IDs.
        
        Args:
            start_id: Starting archer ID
            end_id: Ending archer ID (defaults to config.max_archer_id)
            progress_callback: Optional callback for progress updates
            
        Returns:
            Dictionary with discovery statistics
        """
        end_id = end_id or self.config.max_archer_id

        logger.info(f"Starting archer discovery from ID {start_id} to {end_id}")

        self._reset_stats()
        self._current_log_id = start_sync_log('discovery', total=end_id - start_id + 1)
        
        found_count = 0
        not_found_count = 0
        consecutive_not_found = 0
        
        try:
            for archer_id in range(start_id, end_id + 1):
                if self.is_stopped():
                    logger.info("Discovery stopped by user")
                    break
                
                # Check if we already know about this ID
                existing = get_sync_status(archer_id)
                if existing:
                    self._archers_processed += 1
                    if existing['exists_online']:
                        found_count += 1
                        consecutive_not_found = 0
                    else:
                        not_found_count += 1
                        consecutive_not_found += 1
                    continue
                
                # Fetch archer page
                result = self.fetcher.fetch_archer_page(archer_id)
                self._archers_processed += 1
                
                if result.success and result.content:
                    # Try to parse the page
                    archer_info = parse_archer_page(result.content, archer_id)
                    
                    if archer_info:
                        # Found a valid archer
                        self._save_archer_info(archer_info)
                        found_count += 1
                        consecutive_not_found = 0
                        logger.info(f"Discovered archer {archer_id}: {archer_info.name}")
                    else:
                        # Page exists but couldn't parse
                        mark_archer_not_found(archer_id)
                        not_found_count += 1
                        consecutive_not_found += 1
                        
                elif result.status_code == 404:
                    # Archer doesn't exist
                    mark_archer_not_found(archer_id)
                    not_found_count += 1
                    consecutive_not_found += 1
                    
                else:
                    # Error fetching
                    log_sync_error(
                        archer_id,
                        'http_error',
                        result.error_message or f"HTTP {result.status_code}",
                        http_status=result.status_code,
                        request_url=result.url
                    )
                    self._errors_count += 1
                
                # Progress callback
                if progress_callback:
                    progress_callback(archer_id, end_id)
                
                # Update log periodically
                if self._archers_processed % 100 == 0:
                    self._update_log()
                    logger.info(f"Discovery progress: {archer_id}/{end_id} - Found: {found_count}, Not found: {not_found_count}")
                
            
            status = 'completed' if not self.is_stopped() else 'stopped'
            self._update_log(status=status)
            
        except Exception as e:
            logger.error(f"Discovery error: {e}")
            self._update_log(status='failed', details=str(e))
            raise
        
        return {
            'archers_found': found_count,
            'archers_not_found': not_found_count,
            'errors': self._errors_count,
            'total_processed': self._archers_processed
        }
    
    def discover_new_archers(self) -> Dict[str, Any]:
        """
        Discover new archers by scanning IDs beyond our current max.
        """
        current_max = get_max_external_id()
        start_id = max(1, current_max + 1)
        
        logger.info(f"Discovering new archers starting from ID {start_id}")
        return self.discover_archers(start_id=start_id)
    
    # ==================== Sync Current Season ====================
    
    def sync_current_season(
        self,
        archer_ids: Optional[List[int]] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Dict[str, Any]:
        """
        Sync current season results for archers.
        
        Args:
            archer_ids: Optional list of specific archer IDs to sync
            progress_callback: Optional callback for progress updates
            
        Returns:
            Dictionary with sync statistics
        """
        if archer_ids:
            archers_to_sync = [{'external_id': aid} for aid in archer_ids]
        else:
            archers_to_sync = get_all_sync_status(only_existing=True)
        
        total = len(archers_to_sync)
        logger.info(f"Starting current season sync for {total} archers")

        self._reset_stats()
        self._current_log_id = start_sync_log('daily_sync', total=total)
        
        try:
            for i, archer_status in enumerate(archers_to_sync):
                if self.is_stopped():
                    logger.info("Sync stopped by user")
                    break
                
                external_id = archer_status['external_id']
                
                try:
                    results_added = self._sync_archer_current(external_id)
                    self._results_added += results_added
                    self._archers_processed += 1
                    
                except Exception as e:
                    logger.error(f"Error syncing archer {external_id}: {e}")
                    log_sync_error(
                        external_id,
                        'sync_error',
                        str(e)
                    )
                    self._errors_count += 1
                
                if progress_callback:
                    progress_callback(i + 1, total)
                
                # Update log periodically
                if self._archers_processed % 50 == 0:
                    self._update_log()
                    logger.info(f"Sync progress: {i + 1}/{total} - Results added: {self._results_added}")
            
            status = 'completed' if not self.is_stopped() else 'stopped'
            self._update_log(status=status)
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            self._update_log(status='failed', details=str(e))
            raise
        
        return {
            'archers_processed': self._archers_processed,
            'results_added': self._results_added,
            'errors': self._errors_count
        }
    
    def _sync_archer_current(self, external_id: int) -> int:
        """
        Sync current season results for a single archer.
        
        Returns number of results added.
        """
        result = self.fetcher.fetch_archer_page(external_id)
        
        if not result.success:
            if result.status_code == 404:
                mark_archer_not_found(external_id)
            else:
                log_sync_error(
                    external_id,
                    'http_error',
                    result.error_message or f"HTTP {result.status_code}",
                    http_status=result.status_code,
                    request_url=result.url
                )
            return 0
        
        archer_info = parse_archer_page(result.content, external_id)
        if not archer_info:
            log_sync_error(
                external_id,
                'parse_error',
                "Could not parse archer page",
                request_url=result.url,
                response_body=result.content[:5000] if result.content else None
            )
            return 0
        
        # Save archer and results
        return self._save_archer_info(archer_info)
    
    # ==================== Sync Historical ====================
    
    def sync_historical(
        self,
        archer_ids: Optional[List[int]] = None,
        force: bool = False,
        years: Optional[List[int]] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Dict[str, Any]:
        """
        Sync historical results for archers.
        
        Args:
            archer_ids: Optional list of specific archer IDs to sync
            force: If True, sync even if historical_synced is True
            progress_callback: Optional callback for progress updates
            
        Returns:
            Dictionary with sync statistics
        """
        if archer_ids:
            archers_to_sync = [get_sync_status(aid) for aid in archer_ids]
            archers_to_sync = [a for a in archers_to_sync if a]
        else:
            archers_to_sync = get_all_sync_status(
                only_existing=True,
                only_unsynced_historical=not force
            )
        
        total = len(archers_to_sync)
        logger.info(f"Starting historical sync for {total} archers")

        self._reset_stats()
        self._current_log_id = start_sync_log('historical_sync', total=total)
        
        try:
            for i, archer_status in enumerate(archers_to_sync):
                if self.is_stopped():
                    logger.info("Historical sync stopped by user")
                    break
                
                external_id = archer_status['external_id']
                
                try:
                    results_added = self._sync_archer_historical(external_id, years=years)
                    self._results_added += results_added
                    self._archers_processed += 1
                    
                except Exception as e:
                    logger.error(f"Error syncing historical for archer {external_id}: {e}")
                    log_sync_error(
                        external_id,
                        'historical_sync_error',
                        str(e)
                    )
                    self._errors_count += 1
                
                if progress_callback:
                    progress_callback(i + 1, total)
                
                # Update log periodically
                if self._archers_processed % 20 == 0:
                    self._update_log()
                    logger.info(f"Historical sync progress: {i + 1}/{total} - Results added: {self._results_added}")
            
            status = 'completed' if not self.is_stopped() else 'stopped'
            self._update_log(status=status)
            
        except Exception as e:
            logger.error(f"Historical sync error: {e}")
            self._update_log(status='failed', details=str(e))
            raise
        
        return {
            'archers_processed': self._archers_processed,
            'results_added': self._results_added,
            'errors': self._errors_count
        }
    
    def _sync_archer_historical(self, external_id: int, years: Optional[List[int]] = None) -> int:
        """
        Sync historical results for a single archer.

        Args:
            external_id: The archer's external ID on the website
            years: Explicit list of years to fetch. If None, uses years_available from the page.

        Returns total number of results added.
        """
        # First, get the archer's page to find available years and save current season
        result = self.fetcher.fetch_archer_page(external_id)

        if not result.success:
            return 0

        archer_info = parse_archer_page(result.content, external_id)
        if not archer_info:
            return 0

        # Save current season from the detail page
        total_added = self._save_results(archer_info, archer_info.current_year_results)

        # Determine which years to fetch via GetPreviousSeasonResults
        current_year = datetime.now().year
        years_to_fetch = years if years is not None else archer_info.years_available

        for year in years_to_fetch:
            if self.is_stopped():
                break

            # Current year comes from the detail page; skip it here
            if year >= current_year:
                continue

            hist_result = self.fetcher.fetch_historical_results(external_id, year)

            if hist_result.success and hist_result.content:
                historical_results = parse_result_html(hist_result.content)
                added = self._save_results(archer_info, historical_results)
                total_added += added
                logger.info(f"Archer {external_id} ({archer_info.name}) year {year}: {added} results")
            else:
                logger.warning(f"Archer {external_id} year {year}: fetch failed ({hist_result.error_message})")

        # Mark historical as synced
        upsert_sync_status(
            external_id=external_id,
            historical_synced=True,
            total_results=total_added
        )

        return total_added
    
    # ==================== Helper Methods ====================
    
    def _save_archer_info(self, archer_info: ArcherInfo) -> int:
        """
        Save archer info and current results to database.
        
        Returns number of results added.
        """
        # Add/update archer in database
        archer_id = add_archer_with_external_id(
            name=archer_info.name,
            external_id=archer_info.external_id,
            club=archer_info.club,
            club_id=archer_info.club_id,
            profile_url=archer_info.profile_url
        )
        
        # Save results
        results_added = self._save_results(archer_info, archer_info.current_year_results)
        
        # Update sync status
        latest_date = get_latest_result_date(archer_info.current_year_results)
        years_str = ','.join(str(y) for y in archer_info.years_available) if archer_info.years_available else None
        
        upsert_sync_status(
            external_id=archer_info.external_id,
            archer_id=archer_id,
            name=archer_info.name,
            club=archer_info.club,
            club_id=archer_info.club_id,
            exists_online=True,
            last_result_date=latest_date,
            total_results=len(archer_info.current_year_results),
            years_available=years_str
        )
        
        return results_added
    
    def _save_results(self, archer_info: ArcherInfo, results: List[Dict[str, Any]]) -> int:
        """
        Save results to database.
        
        Returns number of results added.
        """
        if not results:
            return 0
        
        # Get archer ID
        archer_id = add_archer_with_external_id(
            name=archer_info.name,
            external_id=archer_info.external_id,
            club=archer_info.club,
            club_id=archer_info.club_id,
            profile_url=archer_info.profile_url
        )
        
        added = 0
        for r in results:
            try:
                # Add event
                event_id = add_event(
                    event_id=r['event_id'],
                    name=r.get('event_name', ''),
                    event_url=r.get('event_url')
                )
                
                # Add result
                add_result(
                    archer_id=archer_id,
                    event_id=event_id,
                    date=r['date'],
                    distance=r['distance'],
                    category=r['category'],
                    score=r['score'],
                    placement=r.get('placement')
                )
                added += 1
                
            except Exception as e:
                logger.warning(f"Error saving result: {e}")
        
        return added
    
    def run_daily_sync(self):
        """
        Run the daily sync job - sync current season for all archers
        and discover new archers.
        """
        logger.info("Starting daily sync job")
        
        try:
            # First, discover any new archers
            logger.info("Phase 1: Discovering new archers")
            discovery_stats = self.discover_new_archers()
            logger.info(f"Discovery complete: {discovery_stats}")
            
            if self.is_stopped():
                return
            
            # Then sync current season for all known archers
            logger.info("Phase 2: Syncing current season")
            sync_stats = self.sync_current_season()
            logger.info(f"Current season sync complete: {sync_stats}")
            
            # Report overall stats
            stats = get_sync_stats()
            logger.info(f"Daily sync complete. Total archers: {stats['total_archers_found']}, "
                       f"Total results: {stats['total_results']}")
            
        except Exception as e:
            logger.error(f"Daily sync failed: {e}")
            raise
    
    def close(self):
        """Clean up resources."""
        self.fetcher.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def setup_logging(log_level: str = 'INFO', log_file: Optional[str] = None):
    """
    Set up logging for the sync worker.
    """
    import os
    
    handlers = [logging.StreamHandler()]
    
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )


if __name__ == '__main__':
    # Quick test
    init_database()
    setup_logging('DEBUG')
    
    with SyncWorker() as worker:
        # Test discovery of first 10 archers
        stats = worker.discover_archers(start_id=1, end_id=10)
        print(f"Discovery stats: {stats}")
