"""Polymarket CLOB API client for fetching price history."""

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .models import BacktestConfig, Market, PricePoint


class CLOBClient:
    """Async client for Polymarket CLOB API (price history)."""

    def __init__(self, config: BacktestConfig):
        """Initialize CLOB client with configuration."""
        self.config = config
        self.base_url = config.clob_base_url
        self.client: Optional[httpx.AsyncClient] = None
        self.semaphore = asyncio.Semaphore(config.max_concurrent_requests)
        self.cache_dir = Path("data/raw/prices")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self):
        """Enter async context."""
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.request_timeout_seconds),
            limits=httpx.Limits(max_connections=self.config.max_concurrent_requests),
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context."""
        if self.client:
            await self.client.aclose()

    def _get_cache_path(self, token_id: str, snapshot_ts: int) -> Path:
        """Get cache file path for price data."""
        return self.cache_dir / f"{token_id}_{snapshot_ts}.json"

    async def fetch_price_history(
        self,
        token_id: str,
        start_ts: int,
        end_ts: int,
        fidelity: int = 60,
        use_cache: Optional[bool] = None,
    ) -> list[PricePoint]:
        """
        Fetch price history for a token from CLOB API.

        Args:
            token_id: CLOB token ID
            start_ts: Start timestamp (UNIX seconds)
            end_ts: End timestamp (UNIX seconds)
            fidelity: Data granularity in minutes (default 60 = hourly)
            use_cache: Whether to use cached data (None = use config default)

        Returns:
            List of PricePoint objects, sorted by timestamp
        """
        use_cache = use_cache if use_cache is not None else self.config.use_cache
        cache_path = self._get_cache_path(token_id, end_ts)

        # Try cache first
        if use_cache and cache_path.exists():
            try:
                with open(cache_path) as f:
                    raw_history = json.load(f)
                # Handle both response formats from cache
                if isinstance(raw_history, dict) and "history" in raw_history:
                    history_data = raw_history["history"]
                elif isinstance(raw_history, list):
                    history_data = raw_history
                else:
                    history_data = []

                if history_data:
                    return [PricePoint(t=p["t"], p=p["p"]) for p in history_data]
            except Exception:
                pass  # Fall through to API fetch

        # Fetch from API
        async with self.semaphore:
            await asyncio.sleep(self.config.base_sleep_seconds)

            params = {
                "tokenId": token_id,
                "startTs": start_ts,
                "endTs": end_ts,
                "fidelity": fidelity,
            }

            @retry(
                retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
                wait=wait_exponential(multiplier=1, min=1, max=16),
                stop=stop_after_attempt(self.config.max_retries),
                reraise=True,
            )
            async def _make_request():
                response = await self.client.get(
                    f"{self.base_url}/prices-history", params=params
                )

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    await asyncio.sleep(retry_after)
                    response.raise_for_status()

                response.raise_for_status()
                return response.json()

            try:
                raw_history = await _make_request()

                # Cache the result
                if use_cache:
                    try:
                        with open(cache_path, "w") as f:
                            json.dump(raw_history, f)
                    except Exception:
                        pass  # Silently ignore cache failures

                # Parse into PricePoint objects
                # Handle both response formats: {"history": [...]} or [...]
                if isinstance(raw_history, dict) and "history" in raw_history:
                    history_data = raw_history["history"]
                elif isinstance(raw_history, list):
                    history_data = raw_history
                else:
                    return []

                if history_data:
                    price_points = [PricePoint(t=p["t"], p=p["p"]) for p in history_data]
                    return sorted(price_points, key=lambda x: x.timestamp)
                else:
                    return []

            except httpx.HTTPStatusError as e:
                # Silently return empty list for 404s (no price data)
                if e.response.status_code == 404:
                    return []
                # Log other errors
                print(
                    f"⚠️  HTTP error fetching prices for {token_id}: {e.response.status_code}",
                    file=sys.stderr,
                )
                return []
            except httpx.TimeoutException:
                print(f"⚠️  Timeout fetching prices for {token_id}", file=sys.stderr)
                return []
            except Exception as e:
                print(f"⚠️  Unexpected error fetching prices for {token_id}: {e}", file=sys.stderr)
                return []

    def get_snapshot_price(
        self, price_history: list[PricePoint], snapshot_time: datetime
    ) -> Optional[float]:
        """
        Get price at or nearest before snapshot time.

        Args:
            price_history: List of price points
            snapshot_time: Target snapshot timestamp

        Returns:
            Price at snapshot, or None if no suitable price found
        """
        if not price_history:
            return None

        # Find the last price at or before snapshot_time
        valid_prices = [p for p in price_history if p.timestamp <= snapshot_time]

        if not valid_prices:
            # If no price before snapshot, try to get the nearest after
            # (within a small tolerance, e.g., 1 hour)
            tolerance = timedelta(hours=1)
            valid_prices = [
                p
                for p in price_history
                if snapshot_time <= p.timestamp <= snapshot_time + tolerance
            ]

        if not valid_prices:
            return None

        # Return the price closest to snapshot_time
        closest = min(valid_prices, key=lambda p: abs((p.timestamp - snapshot_time).total_seconds()))
        return closest.price

    async def get_market_snapshot_prices(
        self, market: Market, snapshot_time: datetime
    ) -> tuple[Optional[float], Optional[float]]:
        """
        Get YES and NO prices at snapshot time for a market.

        Args:
            market: Market object with CLOB token IDs
            snapshot_time: Target snapshot timestamp

        Returns:
            Tuple of (yes_price, no_price), either may be None if data unavailable
        """
        # Get YES token ID
        yes_idx = market.get_yes_index()
        if yes_idx is None or yes_idx >= len(market.clob_token_ids):
            return None, None

        yes_token_id = market.clob_token_ids[yes_idx]

        # Calculate time window for price fetch
        window_start = snapshot_time - timedelta(hours=self.config.window_hours)
        window_end = snapshot_time + timedelta(hours=1)  # Small buffer after

        # Clamp to market start date if available
        if market.start_date and window_start < market.start_date:
            window_start = market.start_date

        # Fetch YES price history
        start_ts = int(window_start.timestamp())
        end_ts = int(window_end.timestamp())

        try:
            yes_history = await self.fetch_price_history(yes_token_id, start_ts, end_ts)
            yes_price = self.get_snapshot_price(yes_history, snapshot_time)

            if yes_price is None:
                return None, None

            # Derive NO price from YES price
            no_price = 1.0 - yes_price

            # Clamp to valid range [0, 1]
            yes_price = max(0.0, min(1.0, yes_price))
            no_price = max(0.0, min(1.0, no_price))

            return yes_price, no_price

        except Exception as e:
            # Silently return None for failures
            return None, None
