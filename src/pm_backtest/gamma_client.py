"""Polymarket Gamma API client for fetching market data."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm import tqdm

from .models import BacktestConfig, Market


class GammaClient:
    """Async client for Polymarket Gamma API (market metadata)."""

    def __init__(self, config: BacktestConfig):
        """Initialize Gamma client with configuration."""
        self.config = config
        self.base_url = config.gamma_base_url
        self.client: Optional[httpx.AsyncClient] = None
        self.semaphore = asyncio.Semaphore(config.max_concurrent_requests)
        self.cache_dir = Path("data/raw")
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

    def _get_cache_path(self) -> Path:
        """Get cache file path for markets data."""
        today = datetime.now().strftime("%Y%m%d")
        return self.cache_dir / f"markets_{today}.json"

    async def _fetch_markets_page(
        self, limit: int, offset: int, closed: bool = True
    ) -> dict:
        """
        Fetch a single page of markets from Gamma API.

        Args:
            limit: Number of markets per page
            offset: Pagination offset
            closed: Only fetch closed (resolved) markets

        Returns:
            Raw API response as dict
        """
        async with self.semaphore:
            await asyncio.sleep(self.config.base_sleep_seconds)

            params = {"limit": limit, "offset": offset, "closed": str(closed).lower()}

            @retry(
                retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
                wait=wait_exponential(multiplier=1, min=1, max=16),
                stop=stop_after_attempt(self.config.max_retries),
                reraise=True,
            )
            async def _make_request():
                response = await self.client.get(f"{self.base_url}/markets", params=params)

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    print(
                        f"⚠️  Rate limited by Gamma API, waiting {retry_after}s...",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(retry_after)
                    response.raise_for_status()

                response.raise_for_status()
                return response.json()

            try:
                return await _make_request()
            except httpx.HTTPStatusError as e:
                print(f"❌ HTTP error fetching markets: {e}", file=sys.stderr)
                raise
            except httpx.TimeoutException:
                print("❌ Timeout fetching markets from Gamma API", file=sys.stderr)
                raise

    async def fetch_all_markets(self, use_cache: Optional[bool] = None) -> list[Market]:
        """
        Fetch all resolved markets from Gamma API with pagination.

        Args:
            use_cache: Whether to use cached data (None = use config default)

        Returns:
            List of Market objects
        """
        use_cache = use_cache if use_cache is not None else self.config.use_cache
        cache_path = self._get_cache_path()

        # Try to load from cache
        if use_cache and cache_path.exists():
            print(f"📂 Loading markets from cache: {cache_path}")
            try:
                with open(cache_path) as f:
                    raw_markets = json.load(f)
                markets = [self._parse_market(m) for m in raw_markets if m is not None]
                markets = [m for m in markets if m is not None]
                print(f"✓ Loaded {len(markets)} markets from cache\n")
                return markets
            except Exception as e:
                print(f"⚠️  Failed to load cache: {e}, fetching fresh data...\n")

        # Fetch from API
        print("🌐 Fetching markets from Gamma API...")
        all_markets_raw = []
        offset = 0
        limit = self.config.gamma_page_limit

        with tqdm(desc="Fetching markets", unit=" markets") as pbar:
            while True:
                try:
                    response = await self._fetch_markets_page(limit, offset, closed=True)
                except Exception as e:
                    print(f"\n❌ Failed to fetch markets at offset {offset}: {e}")
                    break

                # Response can be a list or dict with data key
                if isinstance(response, list):
                    markets_batch = response
                elif isinstance(response, dict) and "data" in response:
                    markets_batch = response["data"]
                else:
                    markets_batch = []

                if not markets_batch:
                    break

                all_markets_raw.extend(markets_batch)
                pbar.update(len(markets_batch))

                # Check if we got fewer results than limit (last page)
                if len(markets_batch) < limit:
                    break

                offset += limit

        print(f"\n✓ Fetched {len(all_markets_raw)} total markets from API")

        # Cache raw data
        if use_cache:
            try:
                with open(cache_path, "w") as f:
                    json.dump(all_markets_raw, f, indent=2)
                print(f"💾 Cached markets to {cache_path}")
            except Exception as e:
                print(f"⚠️  Failed to cache markets: {e}")

        # Parse into Market objects
        markets = []
        for raw_market in all_markets_raw:
            market = self._parse_market(raw_market)
            if market:
                markets.append(market)

        print(f"✓ Parsed {len(markets)} valid markets\n")
        return markets

    def _parse_market(self, raw: dict) -> Optional[Market]:
        """
        Parse raw market data from API into Market object.

        Args:
            raw: Raw market dict from Gamma API

        Returns:
            Market object or None if parsing fails
        """
        try:
            # Parse JSON string fields
            outcomes = json.loads(raw.get("outcomes", "[]"))
            outcome_prices_str = raw.get("outcomePrices", "[]")
            outcome_prices = json.loads(outcome_prices_str)
            outcome_prices = [float(p) for p in outcome_prices]

            clob_token_ids_str = raw.get("clobTokenIds", "[]")
            clob_token_ids = json.loads(clob_token_ids_str)

            # Create Market object
            market = Market(
                id=raw["id"],
                question=raw.get("question", ""),
                slug=raw.get("slug", ""),
                outcomes=outcomes,
                outcome_prices=outcome_prices,
                clob_token_ids=clob_token_ids,
                end_date=raw["endDate"],
                start_date=raw.get("startDate"),
                liquidityNum=raw.get("liquidityNum", 0.0),
                volumeNum=raw.get("volumeNum", 0.0),
                closed=raw.get("closed", False),
            )

            # Mark as resolved if it has outcome prices
            market.resolved = len(market.outcome_prices) > 0

            return market

        except (KeyError, json.JSONDecodeError, ValueError) as e:
            # Silently skip malformed markets
            return None

    def filter_markets(self, markets: list[Market]) -> list[Market]:
        """
        Filter markets based on backtest criteria.

        Args:
            markets: List of all markets

        Returns:
            Filtered list of markets
        """
        print("🔍 Filtering markets...")

        # Count at each stage
        initial = len(markets)

        # Filter 1: Binary YES/NO markets only
        markets = [m for m in markets if m.is_binary_yes_no()]
        binary = len(markets)

        # Filter 2: Date range
        markets = [
            m
            for m in markets
            if self.config.start_end_date <= m.end_date <= self.config.end_end_date
        ]
        in_date_range = len(markets)

        # Filter 3: Minimum liquidity
        markets = [m for m in markets if m.liquidity >= self.config.min_liquidity]
        with_liquidity = len(markets)

        # Filter 4: Minimum volume
        markets = [m for m in markets if m.volume >= self.config.min_volume]
        final = len(markets)

        # Print filter results
        print(f"  Initial markets:        {initial:5d}")
        print(f"  Binary YES/NO:          {binary:5d} (-{initial - binary})")
        print(
            f"  In date range:          {in_date_range:5d} (-{binary - in_date_range})"
        )
        print(
            f"  Min liquidity ${self.config.min_liquidity:,.0f}: {with_liquidity:5d} "
            f"(-{in_date_range - with_liquidity})"
        )
        print(
            f"  Min volume ${self.config.min_volume:,.0f}:    {final:5d} "
            f"(-{with_liquidity - final})"
        )
        print(f"\n✓ {final} markets match criteria\n")

        return markets
