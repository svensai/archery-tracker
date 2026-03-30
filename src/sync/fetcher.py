"""
HTTP fetcher with rate limiting, retries, and error handling.
"""

import time
import logging
import requests
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from threading import Lock

from .config import SyncConfig, get_config

# Set up logging
logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Result of a fetch operation."""
    success: bool
    status_code: int
    content: Optional[str] = None
    error_message: Optional[str] = None
    url: str = ""
    elapsed_seconds: float = 0.0


class RateLimiter:
    """Simple rate limiter using token bucket algorithm."""
    
    def __init__(self, requests_per_second: float = 1.0):
        self.min_interval = 1.0 / requests_per_second
        self.last_request_time = 0.0
        self.lock = Lock()
    
    def wait(self) -> float:
        """Wait if necessary to respect rate limit. Returns wait time."""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_request_time
            wait_time = max(0, self.min_interval - elapsed)
            
            if wait_time > 0:
                time.sleep(wait_time)
            
            self.last_request_time = time.time()
            return wait_time


class ArcherFetcher:
    """
    Fetches archer data from resultat.bueskyting.no with rate limiting.
    """
    
    def __init__(self, config: Optional[SyncConfig] = None):
        self.config = config or get_config()
        self.rate_limiter = RateLimiter(self.config.requests_per_second)
        self.session = self._create_session()
        self._request_count = 0
        self._error_count = 0
    
    def _create_session(self) -> requests.Session:
        """Create a requests session with appropriate headers."""
        session = requests.Session()
        session.headers.update({
            'User-Agent': self.config.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'nb-NO,nb;q=0.9,no;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        })
        return session
    
    def fetch_archer_page(self, archer_id: int) -> FetchResult:
        """
        Fetch an archer's detail page.
        
        Args:
            archer_id: The external ID of the archer
            
        Returns:
            FetchResult with the page content or error information
        """
        url = self.config.get_archer_url(archer_id)
        return self._fetch_with_retry(url, archer_id)
    
    def fetch_historical_results(self, archer_id: int, year: int) -> FetchResult:
        """
        Fetch historical results for a specific year via AJAX endpoint.
        
        Note: The actual endpoint may need to be discovered by inspecting
        network requests on the website. This is a placeholder.
        
        Args:
            archer_id: The external ID of the archer
            year: The year to fetch results for
            
        Returns:
            FetchResult with the results HTML or error information
        """
        url = self.config.get_historical_url(archer_id, year)
        
        # Add AJAX headers
        extra_headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'text/html, */*; q=0.01',
        }
        
        return self._fetch_with_retry(url, archer_id, extra_headers=extra_headers)
    
    def _fetch_with_retry(
        self, 
        url: str, 
        archer_id: int,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> FetchResult:
        """
        Fetch a URL with retry logic and rate limiting.
        """
        last_error = None
        
        for attempt in range(self.config.max_retries):
            try:
                # Rate limiting
                wait_time = self.rate_limiter.wait()
                if wait_time > 0:
                    logger.debug(f"Rate limited, waited {wait_time:.2f}s")
                
                # Make request
                start_time = time.time()
                
                headers = dict(self.session.headers)
                if extra_headers:
                    headers.update(extra_headers)
                
                response = self.session.get(
                    url,
                    headers=headers,
                    timeout=self.config.request_timeout,
                    allow_redirects=True
                )
                
                elapsed = time.time() - start_time
                self._request_count += 1
                
                # Check for success
                if response.status_code == 200:
                    logger.debug(f"Successfully fetched {url} in {elapsed:.2f}s")
                    return FetchResult(
                        success=True,
                        status_code=200,
                        content=response.text,
                        url=url,
                        elapsed_seconds=elapsed
                    )
                
                # Handle 404 - archer doesn't exist
                elif response.status_code == 404:
                    logger.debug(f"Archer {archer_id} not found (404)")
                    return FetchResult(
                        success=False,
                        status_code=404,
                        error_message="Archer not found",
                        url=url,
                        elapsed_seconds=elapsed
                    )
                
                # Handle other errors
                else:
                    last_error = f"HTTP {response.status_code}"
                    logger.warning(f"Attempt {attempt + 1}: {url} returned {response.status_code}")
                    
            except requests.Timeout as e:
                last_error = f"Timeout: {str(e)}"
                logger.warning(f"Attempt {attempt + 1}: Timeout fetching {url}")
                self._error_count += 1
                
            except requests.ConnectionError as e:
                last_error = f"Connection error: {str(e)}"
                logger.warning(f"Attempt {attempt + 1}: Connection error for {url}")
                self._error_count += 1
                
            except requests.RequestException as e:
                last_error = f"Request error: {str(e)}"
                logger.warning(f"Attempt {attempt + 1}: Request error for {url}: {e}")
                self._error_count += 1
            
            # Exponential backoff before retry
            if attempt < self.config.max_retries - 1:
                delay = self.config.retry_delay_base ** (attempt + 1)
                logger.info(f"Retrying in {delay:.1f}s...")
                time.sleep(delay)
        
        # All retries failed
        logger.error(f"Failed to fetch {url} after {self.config.max_retries} attempts: {last_error}")
        return FetchResult(
            success=False,
            status_code=0,
            error_message=last_error,
            url=url
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get fetcher statistics."""
        return {
            'total_requests': self._request_count,
            'total_errors': self._error_count,
            'error_rate': self._error_count / max(1, self._request_count)
        }
    
    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self._request_count = 0
        self._error_count = 0
    
    def close(self) -> None:
        """Close the session."""
        self.session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# Convenience function for one-off fetches
def fetch_archer(archer_id: int, config: Optional[SyncConfig] = None) -> FetchResult:
    """
    Convenience function to fetch a single archer's page.
    
    For batch operations, use ArcherFetcher class directly to reuse session.
    """
    with ArcherFetcher(config) as fetcher:
        return fetcher.fetch_archer_page(archer_id)
