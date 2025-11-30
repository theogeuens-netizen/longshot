"""Core backtesting logic for longshot bias strategy."""

import asyncio
from datetime import timedelta
from typing import Optional

from tqdm import tqdm

from .clob_client import CLOBClient
from .models import BacktestConfig, Market, Outcome, Snapshot, TradeResult


class LongshotBacktester:
    """Backtest the longshot bias strategy."""

    def __init__(self, config: BacktestConfig, clob_client: CLOBClient):
        """
        Initialize backtester.

        Args:
            config: Backtest configuration
            clob_client: CLOB API client for price data
        """
        self.config = config
        self.clob_client = clob_client

    async def create_snapshots(self, markets: list[Market]) -> list[Snapshot]:
        """
        Create price snapshots for all markets at lookback time before resolution.

        Args:
            markets: List of markets to snapshot

        Returns:
            List of snapshots with price data
        """
        print(f"📸 Creating snapshots ({self.config.lookback_days} days before resolution)...")

        snapshots = []
        tasks = []

        # Create concurrent tasks for all markets
        for market in markets:
            task = self._create_market_snapshot(market)
            tasks.append(task)

        # Execute with progress bar
        with tqdm(total=len(tasks), desc="Fetching prices", unit=" markets") as pbar:
            for coro in asyncio.as_completed(tasks):
                snapshot = await coro
                if snapshot:
                    snapshots.append(snapshot)
                pbar.update(1)

        print(f"\n✓ Created {len(snapshots)} snapshots with valid price data")
        print(f"  ({len(markets) - len(snapshots)} markets had missing price data)\n")

        return snapshots

    async def _create_market_snapshot(self, market: Market) -> Optional[Snapshot]:
        """
        Create a single market snapshot.

        Args:
            market: Market to snapshot

        Returns:
            Snapshot object or None if price data unavailable
        """
        # Calculate snapshot time (lookback days before resolution)
        snapshot_time = market.end_date - timedelta(days=self.config.lookback_days)

        # Get prices at snapshot time
        yes_price, no_price = await self.clob_client.get_market_snapshot_prices(
            market, snapshot_time
        )

        if yes_price is None or no_price is None:
            return None

        # Create snapshot
        snapshot = Snapshot(
            market_id=market.id,
            question=market.question,
            snapshot_time=snapshot_time,
            resolution_time=market.end_date,
            yes_price=yes_price,
            no_price=no_price,
            liquidity=market.liquidity,
            volume=market.volume,
        )

        return snapshot

    def identify_longshots(self, snapshots: list[Snapshot]) -> list[Snapshot]:
        """
        Identify snapshots with longshot opportunities.

        A longshot is when either YES or NO price is between longshot_min and longshot_max.

        Args:
            snapshots: All snapshots

        Returns:
            Snapshots with longshot opportunities (and trade details filled in)
        """
        print("🎯 Identifying longshot opportunities...")

        longshots = []
        yes_longshots = 0
        no_longshots = 0

        for snapshot in snapshots:
            # Check if YES is a longshot (low probability)
            if self.config.longshot_min <= snapshot.yes_price <= self.config.longshot_max:
                snapshot.longshot_outcome = Outcome.YES
                snapshot.bet_on_outcome = Outcome.NO  # Bet against the longshot
                snapshot.entry_price = snapshot.no_price
                snapshot.entered_trade = True
                longshots.append(snapshot)
                yes_longshots += 1

            # Check if NO is a longshot (low probability)
            elif self.config.longshot_min <= snapshot.no_price <= self.config.longshot_max:
                snapshot.longshot_outcome = Outcome.NO
                snapshot.bet_on_outcome = Outcome.YES  # Bet against the longshot
                snapshot.entry_price = snapshot.yes_price
                snapshot.entered_trade = True
                longshots.append(snapshot)
                no_longshots += 1

        print(f"  Total longshots found: {len(longshots)}")
        print(f"    YES longshots (bought NO): {yes_longshots}")
        print(f"    NO longshots (bought YES):  {no_longshots}")
        print()

        return longshots

    def simulate_trades(
        self, longshot_snapshots: list[Snapshot], markets: list[Market]
    ) -> list[TradeResult]:
        """
        Simulate trades for all longshot opportunities.

        Args:
            longshot_snapshots: Snapshots with longshot trades
            markets: All markets (to get resolution data)

        Returns:
            List of trade results with P&L
        """
        print("💰 Simulating trades and calculating P&L...")

        # Create market lookup
        market_lookup = {m.id: m for m in markets}

        trades = []
        for snapshot in tqdm(longshot_snapshots, desc="Simulating trades"):
            market = market_lookup.get(snapshot.market_id)
            if not market:
                continue

            # Get winner
            winner = market.get_winner()
            if winner is None:
                continue  # Skip unresolved or invalid markets

            # Calculate P&L
            win = snapshot.bet_on_outcome == winner
            if win:
                # Won: get back 1.0 per share, paid entry_price
                pnl = 1.0 - snapshot.entry_price
            else:
                # Lost: get back 0, paid entry_price
                pnl = 0.0 - snapshot.entry_price

            return_pct = (pnl / snapshot.entry_price) * 100 if snapshot.entry_price > 0 else 0

            # Create trade result
            trade = TradeResult(
                market_id=market.id,
                question=market.question,
                snapshot_time=snapshot.snapshot_time,
                resolution_time=snapshot.resolution_time,
                longshot_outcome=snapshot.longshot_outcome,
                bet_on_outcome=snapshot.bet_on_outcome,
                entry_price=snapshot.entry_price,
                winner=winner,
                win=win,
                pnl=pnl,
                return_pct=return_pct,
                liquidity=snapshot.liquidity,
                volume=snapshot.volume,
                slug=market.slug,
            )
            trades.append(trade)

        print(f"✓ Simulated {len(trades)} trades\n")
        return trades

    def compute_summary_stats(self, trades: list[TradeResult]) -> dict:
        """
        Compute summary statistics from trades.

        Args:
            trades: List of trade results

        Returns:
            Dictionary of summary statistics
        """
        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_return": 0.0,
                "median_return": 0.0,
                "total_pnl": 0.0,
                "percentile_5": 0.0,
                "percentile_25": 0.0,
                "percentile_75": 0.0,
                "percentile_95": 0.0,
                "yes_longshots": 0,
                "no_longshots": 0,
            }

        # Basic stats
        total_trades = len(trades)
        wins = sum(1 for t in trades if t.win)
        win_rate = (wins / total_trades) * 100

        # Returns
        returns = [t.return_pct for t in trades]
        returns_sorted = sorted(returns)

        avg_return = sum(returns) / len(returns)
        median_return = returns_sorted[len(returns_sorted) // 2]

        # Percentiles
        def percentile(data, p):
            idx = int(len(data) * p / 100)
            return data[min(idx, len(data) - 1)]

        p5 = percentile(returns_sorted, 5)
        p25 = percentile(returns_sorted, 25)
        p75 = percentile(returns_sorted, 75)
        p95 = percentile(returns_sorted, 95)

        # Total P&L
        total_pnl = sum(t.pnl for t in trades)

        # Breakdown
        yes_longshots = sum(1 for t in trades if t.longshot_outcome == Outcome.YES)
        no_longshots = sum(1 for t in trades if t.longshot_outcome == Outcome.NO)

        return {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "avg_return": avg_return,
            "median_return": median_return,
            "total_pnl": total_pnl,
            "percentile_5": p5,
            "percentile_25": p25,
            "percentile_75": p75,
            "percentile_95": p95,
            "yes_longshots": yes_longshots,
            "no_longshots": no_longshots,
        }

    def print_summary(self, stats: dict) -> None:
        """
        Print human-readable summary statistics.

        Args:
            stats: Summary statistics dictionary
        """
        print("\n" + "=" * 60)
        print("📊 BACKTEST RESULTS")
        print("=" * 60)
        print(f"\nStrategy: Bet against longshots (1-10% probability)")
        print(f"Lookback: {self.config.lookback_days} days before resolution")
        print(
            f"Period: {self.config.start_end_date.date()} to {self.config.end_end_date.date()}"
        )
        print(f"\n{'Total Trades:':<25} {stats['total_trades']:>10,}")
        print(f"{'  YES longshots (bought NO):':<25} {stats['yes_longshots']:>10,}")
        print(f"{'  NO longshots (bought YES):':<25} {stats['no_longshots']:>10,}")

        print(f"\n{'Win Rate:':<25} {stats['win_rate']:>9.1f}%")
        print(f"{'Average Return:':<25} {stats['avg_return']:>9.1f}%")
        print(f"{'Median Return:':<25} {stats['median_return']:>9.1f}%")
        print(f"{'Total P&L:':<25} {stats['total_pnl']:>10.3f}")

        print(f"\nReturn Distribution:")
        print(f"{'  5th percentile:':<25} {stats['percentile_5']:>9.1f}%")
        print(f"{'  25th percentile:':<25} {stats['percentile_25']:>9.1f}%")
        print(f"{'  75th percentile:':<25} {stats['percentile_75']:>9.1f}%")
        print(f"{'  95th percentile:':<25} {stats['percentile_95']:>9.1f}%")

        print("\n" + "=" * 60 + "\n")

        # Interpretation
        if stats["total_trades"] > 0:
            if stats["avg_return"] > 5:
                print("✅ Strategy shows positive edge - longshots appear overvalued")
            elif stats["avg_return"] > 0:
                print("⚠️  Strategy shows slight positive edge - needs more data")
            else:
                print("❌ Strategy shows negative edge - longshots may be undervalued")
            print()
