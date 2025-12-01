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
from .database_builder import build_database
from .fast_backtester import print_backtest_summary
from .fast_backtester import run_backtest as run_fast_backtest
from .gamma_client import GammaClient
from .longshot_logic import LongshotBacktester
from .models import BacktestSummary
from .sweep_runner import run_sweep_from_file


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


async def build_db_command(
    config_path: str = "config.yaml",
    start_date: str = None,
    end_date: str = None,
) -> None:
    """
    Build or update the parquet database.

    Args:
        config_path: Path to configuration file
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
    """
    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ {e}\n", file=sys.stderr)
        sys.exit(1)

    # Parse dates if provided
    start_dt = datetime.fromisoformat(start_date) if start_date else None
    end_dt = datetime.fromisoformat(end_date) if end_date else None

    try:
        await build_database(config, start_dt, end_dt)
    except Exception as e:
        print(f"\n❌ Failed to build database: {e}\n", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


def backtest_command(
    lookback: int = 7,
    longshot_min: float = 0.01,
    longshot_max: float = 0.10,
    min_volume: float = 5000,
    fees_bps: int = 200,
    slippage_bps: int = 100,
    start_date: str = None,
    end_date: str = None,
) -> None:
    """
    Run a single fast backtest from parquet database.

    Args:
        lookback: Days before resolution to take snapshot
        longshot_min: Minimum longshot probability
        longshot_max: Maximum longshot probability
        min_volume: Minimum market volume
        fees_bps: Trading fees in basis points
        slippage_bps: Slippage in basis points
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
    """
    # Load database
    db_dir = Path("data/db")
    markets_path = db_dir / "markets.parquet"
    snapshots_path = db_dir / "snapshots.parquet"

    if not markets_path.exists() or not snapshots_path.exists():
        print("\n❌ Database not found. Run 'build-db' first.\n", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print("🎲 FAST BACKTEST")
    print("=" * 60 + "\n")

    print("📂 Loading database...")
    markets_df = pd.read_parquet(markets_path)
    snapshots_df = pd.read_parquet(snapshots_path)
    print(f"   ✓ Loaded {len(markets_df)} markets and {len(snapshots_df)} snapshots\n")

    # Run backtest
    print("🔄 Running backtest...\n")
    trades_df, result = run_fast_backtest(
        markets_df=markets_df,
        snapshots_df=snapshots_df,
        lookback_days=lookback,
        longshot_min=longshot_min,
        longshot_max=longshot_max,
        min_volume=min_volume,
        fees_bps=fees_bps,
        slippage_bps=slippage_bps,
        start_date=start_date,
        end_date=end_date,
    )

    # Print results
    print_backtest_summary(result)

    # Save results
    output_dir = Path("data/processed")
    output_dir.mkdir(parents=True, exist_ok=True)

    if not trades_df.empty:
        trades_path = output_dir / "fast_backtest_trades.csv"
        trades_df.to_csv(trades_path, index=False)
        print(f"💾 Saved trades to {trades_path}\n")


def sweep_command(sweep_config: str) -> None:
    """
    Run a parameter sweep from YAML config.

    Args:
        sweep_config: Path to sweep YAML configuration file
    """
    try:
        run_sweep_from_file(sweep_config)
    except FileNotFoundError as e:
        print(f"\n❌ {e}\n", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Failed to run sweep: {e}\n", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Polymarket Longshot Bias Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run backtest with default config (original slow method)
  python -m pm_backtest.cli run-backtest

  # Build/update the parquet database
  python -m pm_backtest.cli build-db
  python -m pm_backtest.cli build-db --start 2025-04-01 --end 2025-11-01

  # Run a single fast backtest
  python -m pm_backtest.cli backtest --lookback 7 --longshot-min 0.01 --longshot-max 0.10

  # Run a parameter sweep
  python -m pm_backtest.cli sweep sweeps/my_sweep.yaml

For more information, see README.md
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # run-backtest command (original, unchanged)
    backtest_parser = subparsers.add_parser(
        "run-backtest", help="Run the longshot bias backtest (original slow method)"
    )
    backtest_parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )

    # build-db command (new)
    build_db_parser = subparsers.add_parser(
        "build-db", help="Build or update the parquet database"
    )
    build_db_parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    build_db_parser.add_argument(
        "--start",
        help="Start date (YYYY-MM-DD)",
    )
    build_db_parser.add_argument(
        "--end",
        help="End date (YYYY-MM-DD)",
    )

    # backtest command (new fast backtest)
    fast_backtest_parser = subparsers.add_parser(
        "backtest", help="Run a single fast backtest from parquet database"
    )
    fast_backtest_parser.add_argument(
        "--lookback",
        type=int,
        default=7,
        help="Days before resolution to take snapshot (default: 7)",
    )
    fast_backtest_parser.add_argument(
        "--longshot-min",
        type=float,
        default=0.01,
        help="Minimum longshot probability (default: 0.01)",
    )
    fast_backtest_parser.add_argument(
        "--longshot-max",
        type=float,
        default=0.10,
        help="Maximum longshot probability (default: 0.10)",
    )
    fast_backtest_parser.add_argument(
        "--min-volume",
        type=float,
        default=5000,
        help="Minimum market volume (default: 5000)",
    )
    fast_backtest_parser.add_argument(
        "--fees-bps",
        type=int,
        default=200,
        help="Trading fees in basis points (default: 200 = 2%%)",
    )
    fast_backtest_parser.add_argument(
        "--slippage-bps",
        type=int,
        default=100,
        help="Slippage in basis points (default: 100 = 1%%)",
    )
    fast_backtest_parser.add_argument(
        "--start",
        help="Start date (YYYY-MM-DD)",
    )
    fast_backtest_parser.add_argument(
        "--end",
        help="End date (YYYY-MM-DD)",
    )

    # sweep command (new)
    sweep_parser = subparsers.add_parser(
        "sweep", help="Run a parameter sweep from YAML config"
    )
    sweep_parser.add_argument(
        "sweep_config",
        help="Path to sweep YAML configuration file",
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

    elif args.command == "build-db":
        try:
            asyncio.run(build_db_command(args.config, args.start, args.end))
        except KeyboardInterrupt:
            print("\n\n⚠️  Database build interrupted by user\n")
            sys.exit(130)
        except Exception as e:
            print(f"\n❌ Unexpected error: {e}\n", file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)

    elif args.command == "backtest":
        try:
            backtest_command(
                lookback=args.lookback,
                longshot_min=args.longshot_min,
                longshot_max=args.longshot_max,
                min_volume=args.min_volume,
                fees_bps=args.fees_bps,
                slippage_bps=args.slippage_bps,
                start_date=args.start,
                end_date=args.end,
            )
        except KeyboardInterrupt:
            print("\n\n⚠️  Backtest interrupted by user\n")
            sys.exit(130)

    elif args.command == "sweep":
        try:
            sweep_command(args.sweep_config)
        except KeyboardInterrupt:
            print("\n\n⚠️  Sweep interrupted by user\n")
            sys.exit(130)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
