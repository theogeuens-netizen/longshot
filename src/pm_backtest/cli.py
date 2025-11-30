"""Command-line interface for Polymarket longshot backtest."""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from .clob_client import CLOBClient
from .config import load_config
from .gamma_client import GammaClient
from .longshot_logic import LongshotBacktester
from .models import BacktestSummary


async def run_backtest(config_path: str = "config.yaml") -> None:
    """
    Run the full backtest pipeline.

    Args:
        config_path: Path to configuration file
    """
    start_time = time.time()

    print("\n" + "=" * 60)
    print("🎲 POLYMARKET LONGSHOT BIAS BACKTEST")
    print("=" * 60 + "\n")

    # Load configuration
    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ {e}\n", file=sys.stderr)
        sys.exit(1)

    # Step 1: Fetch markets
    print("=" * 60)
    print("STEP 1: Fetch Markets from Gamma API")
    print("=" * 60 + "\n")

    async with GammaClient(config) as gamma_client:
        try:
            all_markets = await gamma_client.fetch_all_markets()
        except Exception as e:
            print(f"\n❌ Failed to fetch markets: {e}\n", file=sys.stderr)
            sys.exit(1)

        # Filter markets
        markets = gamma_client.filter_markets(all_markets)

        if not markets:
            print("❌ No markets match the filter criteria. Exiting.\n")
            sys.exit(1)

    # Step 2: Create snapshots
    print("=" * 60)
    print("STEP 2: Fetch Price Snapshots from CLOB API")
    print("=" * 60 + "\n")

    async with CLOBClient(config) as clob_client:
        backtester = LongshotBacktester(config, clob_client)

        try:
            all_snapshots = await backtester.create_snapshots(markets)
        except Exception as e:
            print(f"\n❌ Failed to create snapshots: {e}\n", file=sys.stderr)
            sys.exit(1)

        if not all_snapshots:
            print("❌ No valid price snapshots found. Exiting.\n")
            sys.exit(1)

    # Step 3: Identify longshots
    print("=" * 60)
    print("STEP 3: Identify Longshot Opportunities")
    print("=" * 60 + "\n")

    longshot_snapshots = backtester.identify_longshots(all_snapshots)

    if not longshot_snapshots:
        print("❌ No longshot opportunities found in this period.\n")
        print("Try adjusting your date range or longshot thresholds in config.yaml\n")
        sys.exit(0)

    # Step 4: Simulate trades
    print("=" * 60)
    print("STEP 4: Simulate Trades & Calculate P&L")
    print("=" * 60 + "\n")

    trades = backtester.simulate_trades(longshot_snapshots, markets)

    if not trades:
        print("❌ No valid trades could be simulated.\n")
        sys.exit(0)

    # Step 5: Compute statistics
    print("=" * 60)
    print("STEP 5: Compute Summary Statistics")
    print("=" * 60)

    stats = backtester.compute_summary_stats(trades)
    backtester.print_summary(stats)

    # Step 6: Export results
    print("=" * 60)
    print("STEP 6: Export Results")
    print("=" * 60 + "\n")

    export_results(trades, stats, config, time.time() - start_time)

    print("✅ Backtest complete!\n")


def export_results(trades, stats: dict, config, run_time: float) -> None:
    """
    Export backtest results to CSV and JSON.

    Args:
        trades: List of TradeResult objects
        stats: Summary statistics dictionary
        config: Backtest configuration
        run_time: Total runtime in seconds
    """
    output_dir = Path("data/processed")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Export trades to CSV
    csv_path = output_dir / "trades.csv"
    print(f"📝 Writing trades to {csv_path}...")

    trades_data = []
    for trade in trades:
        trades_data.append(
            {
                "market_id": trade.market_id,
                "question": trade.question,
                "slug": trade.slug,
                "snapshot_time": trade.snapshot_time.isoformat(),
                "resolution_time": trade.resolution_time.isoformat(),
                "longshot_outcome": trade.longshot_outcome.value,
                "bet_on_outcome": trade.bet_on_outcome.value,
                "entry_price": trade.entry_price,
                "winner": trade.winner.value,
                "win": trade.win,
                "pnl": trade.pnl,
                "return_pct": trade.return_pct,
                "liquidity": trade.liquidity,
                "volume": trade.volume,
            }
        )

    df = pd.DataFrame(trades_data)
    df.to_csv(csv_path, index=False)
    print(f"✓ Saved {len(trades)} trades to {csv_path}")

    # Also save as Parquet for efficient storage
    parquet_path = output_dir / "trades.parquet"
    df.to_parquet(parquet_path, index=False)
    print(f"✓ Saved trades to {parquet_path}")

    # Export summary to JSON
    json_path = output_dir / "summary.json"
    print(f"📝 Writing summary to {json_path}...")

    summary = BacktestSummary(
        total_markets_fetched=0,  # Not tracked in this flow
        total_binary_markets=0,  # Not tracked in this flow
        total_snapshots_attempted=0,  # Not tracked in this flow
        total_trades=stats["total_trades"],
        win_rate=stats["win_rate"],
        avg_return=stats["avg_return"],
        median_return=stats["median_return"],
        total_pnl=stats["total_pnl"],
        percentile_5=stats["percentile_5"],
        percentile_25=stats["percentile_25"],
        percentile_75=stats["percentile_75"],
        percentile_95=stats["percentile_95"],
        yes_longshots=stats["yes_longshots"],
        no_longshots=stats["no_longshots"],
        backtest_start_date=config.start_end_date,
        backtest_end_date=config.end_end_date,
        run_time_seconds=run_time,
    )

    with open(json_path, "w") as f:
        json.dump(summary.model_dump(mode="json"), f, indent=2, default=str)

    print(f"✓ Saved summary to {json_path}\n")


def main():
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Polymarket Longshot Bias Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run backtest with default config
  python -m pm_backtest.cli run-backtest

  # Run with custom config file
  python -m pm_backtest.cli run-backtest --config my_config.yaml

For more information, see README.md
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # run-backtest command
    backtest_parser = subparsers.add_parser(
        "run-backtest", help="Run the longshot bias backtest"
    )
    backtest_parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )

    args = parser.parse_args()

    if args.command == "run-backtest":
        try:
            asyncio.run(run_backtest(args.config))
        except KeyboardInterrupt:
            print("\n\n⚠️  Backtest interrupted by user\n")
            sys.exit(130)
        except Exception as e:
            print(f"\n❌ Unexpected error: {e}\n", file=sys.stderr)
            import traceback

            traceback.print_exc()
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
