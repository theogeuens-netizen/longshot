"""Parameter sweep runner for fast backtesting.

Reads YAML configuration and runs multiple backtests with different parameters.
"""

import itertools
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml
from pydantic import BaseModel

from .fast_backtester import BacktestResult, run_backtest


class SweepConfig(BaseModel):
    """Configuration for a parameter sweep."""

    name: str
    description: str
    date_range: dict[str, str]
    fixed: dict[str, Any]
    vary: dict[str, list[Any]]


class SweepRunner:
    """Run parameter sweeps for backtesting."""

    def __init__(self, sweep_config_path: str):
        """
        Initialize sweep runner.

        Args:
            sweep_config_path: Path to sweep YAML configuration file
        """
        self.sweep_config_path = Path(sweep_config_path)
        self.config = self._load_config()

    def _load_config(self) -> SweepConfig:
        """
        Load sweep configuration from YAML file.

        Returns:
            SweepConfig object

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        if not self.sweep_config_path.exists():
            raise FileNotFoundError(f"Sweep config not found: {self.sweep_config_path}")

        with open(self.sweep_config_path) as f:
            raw_config = yaml.safe_load(f)

        try:
            config = SweepConfig(**raw_config)
        except Exception as e:
            raise ValueError(f"Invalid sweep config: {e}")

        return config

    def generate_parameter_combinations(self) -> list[dict[str, Any]]:
        """
        Generate all parameter combinations from sweep config.

        Returns:
            List of parameter dictionaries
        """
        # Start with fixed parameters
        base_params = self.config.fixed.copy()

        # Add date range
        base_params["start_date"] = self.config.date_range.get("start")
        base_params["end_date"] = self.config.date_range.get("end")

        # Handle varying parameters
        if not self.config.vary:
            return [base_params]

        # Special handling for longshot_range (list of [min, max] pairs)
        vary_params = {}
        for key, values in self.config.vary.items():
            if key == "longshot_range":
                # Convert to separate min/max parameters
                vary_params["_longshot_ranges"] = values
            else:
                vary_params[key] = values

        # Generate all combinations
        if not vary_params:
            return [base_params]

        # Get all keys and their value lists
        keys = list(vary_params.keys())
        value_lists = [vary_params[k] for k in keys]

        # Generate cartesian product
        combinations = []
        for values in itertools.product(*value_lists):
            params = base_params.copy()

            for key, value in zip(keys, values):
                if key == "_longshot_ranges":
                    # Expand [min, max] into separate parameters
                    params["longshot_min"] = value[0]
                    params["longshot_max"] = value[1]
                else:
                    params[key] = value

            combinations.append(params)

        return combinations

    def run_sweep(
        self, markets_df: pd.DataFrame, snapshots_df: pd.DataFrame
    ) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
        """
        Run all parameter combinations and collect results.

        Args:
            markets_df: Markets DataFrame from database
            snapshots_df: Snapshots DataFrame from database

        Returns:
            Tuple of (summary_df, trades_list)
                summary_df: DataFrame with one row per run
                trades_list: List of trades DataFrames (one per run)
        """
        print("\n" + "=" * 60)
        print(f"🔬 PARAMETER SWEEP: {self.config.name}")
        print("=" * 60)
        print(f"\n{self.config.description}\n")

        combinations = self.generate_parameter_combinations()

        print(f"Running {len(combinations)} parameter combination(s)...\n")

        summary_rows = []
        trades_list = []

        for run_num, params in enumerate(combinations, 1):
            print(f"Run {run_num}/{len(combinations)}: {self._format_params(params)}")

            # Run backtest
            trades_df, result = run_backtest(
                markets_df=markets_df,
                snapshots_df=snapshots_df,
                lookback_days=params.get("lookback_days", 7),
                longshot_min=params.get("longshot_min", 0.01),
                longshot_max=params.get("longshot_max", 0.10),
                min_volume=params.get("min_volume", 5000),
                fees_bps=params.get("fees_bps", 200),
                slippage_bps=params.get("slippage_bps", 100),
                start_date=params.get("start_date"),
                end_date=params.get("end_date"),
                categories=params.get("categories"),
            )

            # Add to summary
            summary_row = {
                "run": run_num,
                "lookback_days": result.lookback_days,
                "longshot_min": result.longshot_min,
                "longshot_max": result.longshot_max,
                "min_volume": result.min_volume,
                "fees_bps": result.fees_bps,
                "slippage_bps": result.slippage_bps,
                "total_markets": result.total_markets,
                "total_trades": result.total_trades,
                "win_rate": result.win_rate,
                "avg_return": result.avg_return,
                "median_return": result.median_return,
                "total_pnl_gross": result.total_pnl,
                "total_pnl_net": result.total_pnl_with_costs,
                "percentile_5": result.percentile_5,
                "percentile_25": result.percentile_25,
                "percentile_75": result.percentile_75,
                "percentile_95": result.percentile_95,
                "yes_longshots": result.yes_longshots,
                "no_longshots": result.no_longshots,
            }

            summary_rows.append(summary_row)
            trades_list.append(trades_df)

            print(
                f"  → {result.total_trades} trades, "
                f"{result.win_rate:.1f}% win rate, "
                f"{result.avg_return:+.1f}% avg return\n"
            )

        summary_df = pd.DataFrame(summary_rows)

        print("=" * 60)
        print("✅ Sweep complete!")
        print("=" * 60 + "\n")

        return summary_df, trades_list

    def _format_params(self, params: dict[str, Any]) -> str:
        """Format parameters for display."""
        parts = []

        if "lookback_days" in params:
            parts.append(f"lookback={params['lookback_days']}d")

        if "longshot_min" in params and "longshot_max" in params:
            parts.append(
                f"longshot=[{params['longshot_min']*100:.0f}%-{params['longshot_max']*100:.0f}%]"
            )

        if "min_volume" in params:
            parts.append(f"vol≥${params['min_volume']:,.0f}")

        return ", ".join(parts) if parts else "default"

    def save_results(
        self, summary_df: pd.DataFrame, trades_list: list[pd.DataFrame]
    ) -> Path:
        """
        Save sweep results to files.

        Args:
            summary_df: Summary DataFrame
            trades_list: List of trades DataFrames

        Returns:
            Path to results directory
        """
        # Create results directory
        results_dir = Path("data/results") / self.config.name
        results_dir.mkdir(parents=True, exist_ok=True)

        print(f"💾 Saving results to {results_dir}/")

        # Save summary
        summary_path = results_dir / "summary.csv"
        summary_df.to_csv(summary_path, index=False)
        print(f"   ✓ Saved summary to summary.csv")

        # Save individual trades
        for run_num, trades_df in enumerate(trades_list, 1):
            if not trades_df.empty:
                trades_path = results_dir / f"run_{run_num:03d}_trades.csv"
                trades_df.to_csv(trades_path, index=False)

        print(f"   ✓ Saved {len(trades_list)} trade file(s)")
        print()

        return results_dir

    def print_summary_table(self, summary_df: pd.DataFrame) -> None:
        """
        Print summary table to console.

        Args:
            summary_df: Summary DataFrame
        """
        print("=" * 60)
        print("📊 SWEEP SUMMARY TABLE")
        print("=" * 60 + "\n")

        # Select key columns for display
        display_cols = [
            "run",
            "lookback_days",
            "longshot_min",
            "longshot_max",
            "total_trades",
            "win_rate",
            "avg_return",
            "total_pnl_net",
        ]

        # Format for display
        display_df = summary_df[display_cols].copy()
        display_df["longshot_min"] = display_df["longshot_min"] * 100
        display_df["longshot_max"] = display_df["longshot_max"] * 100

        # Rename columns
        display_df.columns = [
            "Run",
            "Lookback",
            "LS Min %",
            "LS Max %",
            "Trades",
            "Win %",
            "Avg Ret %",
            "Net P&L",
        ]

        # Sort by average return (descending)
        display_df = display_df.sort_values("Avg Ret %", ascending=False)

        print(display_df.to_string(index=False))
        print()

        # Highlight best run
        best_run = summary_df.loc[summary_df["avg_return"].idxmax()]
        print(f"🏆 Best run: #{int(best_run['run'])}")
        print(f"   Lookback: {int(best_run['lookback_days'])} days")
        print(
            f"   Longshot: {best_run['longshot_min']*100:.0f}%-{best_run['longshot_max']*100:.0f}%"
        )
        print(f"   Avg return: {best_run['avg_return']:.2f}%")
        print(f"   Win rate: {best_run['win_rate']:.1f}%")
        print(f"   Total trades: {int(best_run['total_trades'])}")
        print()


def run_sweep_from_file(
    sweep_config_path: str,
    markets_df: Optional[pd.DataFrame] = None,
    snapshots_df: Optional[pd.DataFrame] = None,
) -> None:
    """
    Run a parameter sweep from a YAML config file.

    Args:
        sweep_config_path: Path to sweep YAML configuration
        markets_df: Optional markets DataFrame (if None, loads from parquet)
        snapshots_df: Optional snapshots DataFrame (if None, loads from parquet)
    """
    # Load database if not provided
    if markets_df is None or snapshots_df is None:
        db_dir = Path("data/db")
        markets_path = db_dir / "markets.parquet"
        snapshots_path = db_dir / "snapshots.parquet"

        if not markets_path.exists() or not snapshots_path.exists():
            print("❌ Database not found. Run 'build-db' first.\n")
            return

        print("📂 Loading database...")
        markets_df = pd.read_parquet(markets_path)
        snapshots_df = pd.read_parquet(snapshots_path)
        print(f"   ✓ Loaded {len(markets_df)} markets and {len(snapshots_df)} snapshots\n")

    # Run sweep
    runner = SweepRunner(sweep_config_path)
    summary_df, trades_list = runner.run_sweep(markets_df, snapshots_df)

    # Save results
    results_dir = runner.save_results(summary_df, trades_list)

    # Print summary table
    runner.print_summary_table(summary_df)

    print(f"✅ Results saved to {results_dir}/\n")
