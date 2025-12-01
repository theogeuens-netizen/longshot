"""Database builder for Polymarket backtest data.

Converts cached Gamma and CLOB data into parquet files for fast backtesting.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from tqdm import tqdm

from .clob_client import CLOBClient
from .models import BacktestConfig


class DatabaseBuilder:
    """Build parquet database from cached market and price data."""

    def __init__(self, config: BacktestConfig, clob_client: CLOBClient):
        """
        Initialize database builder.

        Args:
            config: Backtest configuration
            clob_client: CLOB API client for price fetching
        """
        self.config = config
        self.clob_client = clob_client
        self.db_dir = Path("data/db")
        self.db_dir.mkdir(parents=True, exist_ok=True)

    def load_cached_markets(self) -> list[dict]:
        """
        Load markets from cached Gamma data files.

        Returns:
            List of raw market dictionaries from all cache files
        """
        print("📂 Loading markets from Gamma cache...")
        cache_dir = Path("data/raw")

        # Find all markets_*.json files
        cache_files = sorted(cache_dir.glob("markets_*.json"))

        if not cache_files:
            print("❌ No cached market files found in data/raw/")
            print("   Expected files like: markets_YYYYMMDD.json")
            return []

        print(f"   Found {len(cache_files)} cache file(s)")

        all_markets = []
        seen_ids = set()

        for cache_file in cache_files:
            try:
                with open(cache_file) as f:
                    markets = json.load(f)

                # Deduplicate by market ID
                for market in markets:
                    market_id = market.get("id")
                    if market_id and market_id not in seen_ids:
                        all_markets.append(market)
                        seen_ids.add(market_id)

                print(f"   Loaded {len(markets)} markets from {cache_file.name}")

            except Exception as e:
                print(f"⚠️  Failed to load {cache_file}: {e}", file=sys.stderr)

        print(f"✓ Loaded {len(all_markets)} unique markets\n")
        return all_markets

    def filter_and_parse_markets(
        self,
        raw_markets: list[dict],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Filter and parse raw markets into DataFrame.

        Args:
            raw_markets: List of raw market dictionaries
            start_date: Optional start date filter (market end_date >= start_date)
            end_date: Optional end date filter (market end_date <= end_date)

        Returns:
            DataFrame with columns: market_id, question, slug, category, end_date,
                                   volume, winner, clob_token_id
        """
        print("🔍 Filtering and parsing markets...")

        parsed_markets = []

        for raw in tqdm(raw_markets, desc="Parsing markets"):
            try:
                # Parse basic fields
                market_id = raw.get("id")
                question = raw.get("question", "")
                slug = raw.get("slug", "")
                category = raw.get("category", "")

                # Parse datetime
                end_date_str = raw.get("endDate")
                if not end_date_str:
                    continue

                end_date_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))

                # Apply date filters
                if start_date and end_date_dt < start_date:
                    continue
                if end_date and end_date_dt > end_date:
                    continue

                # Parse outcomes
                outcomes_str = raw.get("outcomes", "[]")
                outcomes = json.loads(outcomes_str)

                # Only binary YES/NO markets
                outcomes_lower = {o.lower() for o in outcomes}
                if outcomes_lower != {"yes", "no"}:
                    continue

                # Get volume
                volume = float(raw.get("volumeNum", 0.0))

                # Parse outcome prices to determine winner
                outcome_prices_str = raw.get("outcomePrices", "[]")
                outcome_prices = json.loads(outcome_prices_str)
                outcome_prices = [float(p) for p in outcome_prices]

                winner = None
                if len(outcome_prices) == 2:
                    # Find YES index
                    yes_idx = None
                    for i, outcome in enumerate(outcomes):
                        if outcome.lower() == "yes":
                            yes_idx = i
                            break

                    if yes_idx is not None:
                        yes_price = outcome_prices[yes_idx]
                        no_price = outcome_prices[1 - yes_idx]

                        # Winner should be very close to 1.0 (>0.95)
                        if yes_price > 0.95:
                            winner = "YES"
                        elif no_price > 0.95:
                            winner = "NO"

                # Parse CLOB token IDs (get YES token)
                clob_token_ids_str = raw.get("clobTokenIds", "[]")
                clob_token_ids = json.loads(clob_token_ids_str)

                clob_token_id = None
                if len(clob_token_ids) >= 2:
                    # Find YES token index
                    yes_idx = None
                    for i, outcome in enumerate(outcomes):
                        if outcome.lower() == "yes":
                            yes_idx = i
                            break

                    if yes_idx is not None and yes_idx < len(clob_token_ids):
                        clob_token_id = str(clob_token_ids[yes_idx])

                if not clob_token_id:
                    continue

                parsed_markets.append(
                    {
                        "market_id": market_id,
                        "question": question,
                        "slug": slug,
                        "category": category,
                        "end_date": end_date_dt,
                        "volume": volume,
                        "winner": winner,
                        "clob_token_id": clob_token_id,
                    }
                )

            except Exception as e:
                # Skip malformed markets
                continue

        df = pd.DataFrame(parsed_markets)
        print(f"✓ Parsed {len(df)} valid binary YES/NO markets\n")

        return df

    async def fetch_snapshots_for_markets(
        self,
        markets_df: pd.DataFrame,
        lookback_days_list: list[int],
        existing_snapshots_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """
        Fetch price snapshots for all markets at multiple lookback periods.

        Args:
            markets_df: DataFrame of markets
            lookback_days_list: List of lookback periods (e.g., [3, 7, 14, 30])
            existing_snapshots_df: Optional existing snapshots to skip duplicates

        Returns:
            DataFrame with columns: market_id, lookback_days, snapshot_time,
                                   yes_price, no_price
        """
        print(f"📸 Fetching price snapshots for {len(markets_df)} markets...")
        print(f"   Lookback periods: {lookback_days_list} days")

        # Track existing snapshots to avoid re-fetching
        existing_keys = set()
        if existing_snapshots_df is not None and len(existing_snapshots_df) > 0:
            for _, row in existing_snapshots_df.iterrows():
                key = (row["market_id"], row["lookback_days"])
                existing_keys.add(key)
            print(f"   Skipping {len(existing_keys)} existing snapshots")

        snapshots = []
        tasks = []

        # Create tasks for all market-lookback combinations
        for _, market_row in markets_df.iterrows():
            for lookback_days in lookback_days_list:
                # Skip if already exists
                key = (market_row["market_id"], lookback_days)
                if key in existing_keys:
                    continue

                task = self._fetch_market_snapshot(
                    market_id=market_row["market_id"],
                    clob_token_id=market_row["clob_token_id"],
                    end_date=market_row["end_date"],
                    lookback_days=lookback_days,
                )
                tasks.append(task)

        if not tasks:
            print("✓ All snapshots already exist in database\n")
            return pd.DataFrame()

        print(f"   Fetching {len(tasks)} new snapshot(s)...\n")

        # Execute with progress bar
        with tqdm(total=len(tasks), desc="Fetching prices", unit=" snapshots") as pbar:
            for coro in asyncio.as_completed(tasks):
                snapshot = await coro
                if snapshot:
                    snapshots.append(snapshot)
                pbar.update(1)

        df = pd.DataFrame(snapshots)
        print(f"\n✓ Fetched {len(df)} new valid snapshots")
        print(f"  ({len(tasks) - len(df)} had missing price data)\n")

        return df

    async def _fetch_market_snapshot(
        self,
        market_id: str,
        clob_token_id: str,
        end_date: datetime,
        lookback_days: int,
    ) -> Optional[dict]:
        """
        Fetch price snapshot for a single market-lookback combination.

        Args:
            market_id: Market ID
            clob_token_id: CLOB token ID (YES token)
            end_date: Market end date
            lookback_days: Days before end_date to snapshot

        Returns:
            Dictionary with snapshot data, or None if price unavailable
        """
        snapshot_time = end_date - timedelta(days=lookback_days)

        # Time window for price fetch
        window_start = snapshot_time - timedelta(hours=self.config.window_hours)
        window_end = snapshot_time + timedelta(hours=1)

        start_ts = int(window_start.timestamp())
        end_ts = int(window_end.timestamp())

        try:
            # Fetch YES price history
            yes_history = await self.clob_client.fetch_price_history(
                clob_token_id, start_ts, end_ts
            )

            # Get price at snapshot time
            yes_price = self.clob_client.get_snapshot_price(yes_history, snapshot_time)

            if yes_price is None:
                return None

            # Derive NO price
            no_price = 1.0 - yes_price

            # Clamp to [0, 1]
            yes_price = max(0.0, min(1.0, yes_price))
            no_price = max(0.0, min(1.0, no_price))

            return {
                "market_id": market_id,
                "lookback_days": lookback_days,
                "snapshot_time": snapshot_time,
                "yes_price": yes_price,
                "no_price": no_price,
            }

        except Exception:
            return None

    def save_to_parquet(
        self, markets_df: pd.DataFrame, snapshots_df: pd.DataFrame
    ) -> tuple[Path, Path]:
        """
        Save markets and snapshots to parquet files.

        Args:
            markets_df: Markets DataFrame
            snapshots_df: Snapshots DataFrame

        Returns:
            Tuple of (markets_path, snapshots_path)
        """
        print("💾 Saving to parquet files...")

        markets_path = self.db_dir / "markets.parquet"
        snapshots_path = self.db_dir / "snapshots.parquet"

        markets_df.to_parquet(markets_path, index=False)
        print(f"   ✓ Saved {len(markets_df)} markets to {markets_path}")

        snapshots_df.to_parquet(snapshots_path, index=False)
        print(f"   ✓ Saved {len(snapshots_df)} snapshots to {snapshots_path}")

        print()
        return markets_path, snapshots_path

    def load_existing_database(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Load existing parquet database if it exists.

        Returns:
            Tuple of (markets_df, snapshots_df), empty DataFrames if files don't exist
        """
        markets_path = self.db_dir / "markets.parquet"
        snapshots_path = self.db_dir / "snapshots.parquet"

        markets_df = pd.DataFrame()
        snapshots_df = pd.DataFrame()

        if markets_path.exists():
            markets_df = pd.read_parquet(markets_path)
            print(f"📂 Loaded {len(markets_df)} markets from existing database")

        if snapshots_path.exists():
            snapshots_df = pd.read_parquet(snapshots_path)
            print(f"📂 Loaded {len(snapshots_df)} snapshots from existing database")

        if not markets_df.empty or not snapshots_df.empty:
            print()

        return markets_df, snapshots_df


async def build_database(
    config: BacktestConfig,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    lookback_days_list: Optional[list[int]] = None,
    incremental: bool = True,
) -> None:
    """
    Build or update the parquet database.

    Args:
        config: Backtest configuration
        start_date: Optional start date filter for markets
        end_date: Optional end date filter for markets
        lookback_days_list: List of lookback periods (default: [3, 7, 14, 30])
        incremental: If True, skip markets already in database
    """
    print("\n" + "=" * 60)
    print("🔨 DATABASE BUILDER")
    print("=" * 60 + "\n")

    if lookback_days_list is None:
        lookback_days_list = [3, 7, 14, 30]

    async with CLOBClient(config) as clob_client:
        builder = DatabaseBuilder(config, clob_client)

        # Load existing database if incremental
        existing_markets_df = pd.DataFrame()
        existing_snapshots_df = pd.DataFrame()

        if incremental:
            existing_markets_df, existing_snapshots_df = builder.load_existing_database()

        # Load cached markets from Gamma
        raw_markets = builder.load_cached_markets()

        if not raw_markets:
            print("❌ No cached markets found. Please run 'run-backtest' first to cache data.\n")
            return

        # Filter and parse markets
        new_markets_df = builder.filter_and_parse_markets(raw_markets, start_date, end_date)

        if new_markets_df.empty:
            print("❌ No markets match the filter criteria.\n")
            return

        # Merge with existing markets
        if not existing_markets_df.empty and incremental:
            # Keep existing markets and add new ones
            existing_ids = set(existing_markets_df["market_id"])
            new_only = new_markets_df[~new_markets_df["market_id"].isin(existing_ids)]

            print(f"   Found {len(new_only)} new markets to add")
            print(f"   Keeping {len(existing_markets_df)} existing markets")

            markets_df = pd.concat([existing_markets_df, new_only], ignore_index=True)
        else:
            markets_df = new_markets_df

        # Fetch snapshots
        new_snapshots_df = await builder.fetch_snapshots_for_markets(
            markets_df if not incremental else new_only if not existing_markets_df.empty else markets_df,
            lookback_days_list,
            existing_snapshots_df if incremental else None,
        )

        # Merge snapshots
        if not existing_snapshots_df.empty and not new_snapshots_df.empty:
            snapshots_df = pd.concat(
                [existing_snapshots_df, new_snapshots_df], ignore_index=True
            )
        elif not new_snapshots_df.empty:
            snapshots_df = new_snapshots_df
        else:
            snapshots_df = existing_snapshots_df

        if snapshots_df.empty:
            print("❌ No snapshots available.\n")
            return

        # Save to parquet
        builder.save_to_parquet(markets_df, snapshots_df)

        # Print summary
        print("=" * 60)
        print("📊 DATABASE SUMMARY")
        print("=" * 60)
        print(f"Markets:   {len(markets_df):,}")
        print(f"Snapshots: {len(snapshots_df):,}")
        print(f"Lookback periods: {lookback_days_list}")
        print()
        print("✅ Database build complete!")
        print("   Use 'backtest' or 'sweep' commands to run fast backtests\n")
