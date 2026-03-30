"""
Configuration for the sync module.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class SyncConfig:
    """Configuration settings for archery data synchronization."""
    
    # Base URL for the archery results website
    base_url: str = "https://resultat.bueskyting.no"
    
    # Archer details URL pattern (append archer_id)
    archer_url_pattern: str = "/Archer/Details/{archer_id}"
    
    # Historical results API endpoint
    historical_api_pattern: str = "/Archer/GetPreviousSeasonResults"
    
    # Rate limiting
    requests_per_second: float = 1.0  # 1 request per second
    request_timeout: int = 30  # seconds
    
    # Retry settings
    max_retries: int = 3
    retry_delay_base: float = 2.0  # Base delay for exponential backoff
    
    # Discovery settings
    max_archer_id: int = 7000  # Maximum ID to scan for new archers
    discovery_batch_size: int = 100  # How many IDs to check per batch
    
    # Sync schedule (for daily sync)
    sync_hour: int = 3  # 3 AM
    sync_minute: int = 0
    
    # Logging
    log_file: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        'logs', 'sync.log'
    )
    log_level: str = "INFO"
    
    # User agent for requests
    user_agent: str = "ArcheryTracker/1.0 (Data collection for personal archery statistics)"
    
    def get_archer_url(self, archer_id: int) -> str:
        """Get the full URL for an archer's detail page."""
        return self.base_url + self.archer_url_pattern.format(archer_id=archer_id)
    
    def get_historical_url(self, archer_id: int, year: int) -> str:
        """Get URL for historical results AJAX call."""
        import time as _time
        ts = int(_time.time())
        return f"{self.base_url}{self.historical_api_pattern}?id={archer_id}&year={year}&_={ts}"


# Global config instance
config = SyncConfig()


def get_config() -> SyncConfig:
    """Get the global configuration instance."""
    return config


def update_config(**kwargs) -> SyncConfig:
    """Update configuration settings."""
    global config
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
    return config
