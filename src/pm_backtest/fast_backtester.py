"""Fast backtester using parquet database (no API calls).

Runs backtests entirely from precomputed parquet files for speed.
"""

from datetime import datetime
from typing import Optional

import pandas as pd
from pydantic import BaseModel


class BacktestResult(BaseModel):
    """Result from a fast backtest run."""

    # Parameters
    lookback_days: int
    longshot_min: float
    longshot_max: float
    min_volume: float
    fees_bps: int
    slippage_bps: int
    start_date: Optional[str]
    end_date: Optional[str]
    categories: Optional[list[str]]

    # Results
    total_trades: int
    total_markets: int
    win_rate: float
    avg_return: float
    median_return: float
    total_pnl: float
    total_pnl_with_costs: float

    # Distribution
    percentile_5: float
    percentile_25: float
    percentile_75: float
    percentile_95: float

    # Breakdown
    yes_longshots: int
    no_longshots: int

    # Trades dataframe (stored separately)
    trades_count: int


def run_backtest(
    markets_df: pd.DataFrame,
    snapshots_df: pd.DataFrame,
    lookback_days: int = 7,
    longshot_min: float = 0.01,
    longshot_max: float = 0.10,
    min_volume: float = 5000,
    fees_bps: int = 200,
    slippage_bps: int = 100,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    categories: Optional[list[str]] = None,
) -> tuple[pd.DataFrame, BacktestResult]:
    """
    Run backtest from parquet data.

    Strategy: Identify longshots (YES or NO price in [longshot_min, longshot_max])
              and bet against them (buy the opposite outcome).

    Args:
        markets_df: Markets DataFrame from database
        snapshots_df: Snapshots DataFrame from database
        lookback_days: Days before resolution to take snapshot
        longshot_min: Minimum price for longshot (e.g., 0.01 = 1%)
        longshot_max: Maximum price for longshot (e.g., 0.10 = 10%)
        min_volume: Minimum market volume filter
        fees_bps: Trading fees in basis points (e.g., 200 = 2%)
        slippage_bps: Slippage in basis points (e.g., 100 = 1%)
        start_date: Optional start date filter (YYYY-MM-DD)
        end_date: Optional end date filter (YYYY-MM-DD)
        categories: Optional list of categories to include (None = all)

    Returns:
        Tuple of (trades_df, result_summary)
            trades_df: DataFrame with one row per trade
            result_summary: BacktestResult with summary statistics
    """
    # Filter markets by criteria
    filtered_markets = markets_df.copy()

    # Volume filter
    filtered_markets = filtered_markets[filtered_markets["volume"] >= min_volume]

    # Date filters
    if start_date:
        start_dt = pd.to_datetime(start_date)
        filtered_markets = filtered_markets[filtered_markets["end_date"] >= start_dt]

    if end_date:
        end_dt = pd.to_datetime(end_date)
        filtered_markets = filtered_markets[filtered_markets["end_date"] <= end_dt]

    # Category filter
    if categories:
        filtered_markets = filtered_markets[filtered_markets["category"].isin(categories)]

    # Only resolved markets (winner is not null)
    filtered_markets = filtered_markets[filtered_markets["winner"].notna()]

    total_markets = len(filtered_markets)

    if total_markets == 0:
        # Return empty result
        return pd.DataFrame(), BacktestResult(
            lookback_days=lookback_days,
            longshot_min=longshot_min,
            longshot_max=longshot_max,
            min_volume=min_volume,
            fees_bps=fees_bps,
            slippage_bps=slippage_bps,
            start_date=start_date,
            end_date=end_date,
            categories=categories,
            total_trades=0,
            total_markets=0,
            win_rate=0.0,
            avg_return=0.0,
            median_return=0.0,
            total_pnl=0.0,
            total_pnl_with_costs=0.0,
            percentile_5=0.0,
            percentile_25=0.0,
            percentile_75=0.0,
            percentile_95=0.0,
            yes_longshots=0,
            no_longshots=0,
            trades_count=0,
        )

    # Filter snapshots by lookback_days
    filtered_snapshots = snapshots_df[snapshots_df["lookback_days"] == lookback_days]

    # Join markets + snapshots
    data = filtered_markets.merge(
        filtered_snapshots, on="market_id", how="inner"
    )

    if len(data) == 0:
        # Return empty result
        return pd.DataFrame(), BacktestResult(
            lookback_days=lookback_days,
            longshot_min=longshot_min,
            longshot_max=longshot_max,
            min_volume=min_volume,
            fees_bps=fees_bps,
            slippage_bps=slippage_bps,
            start_date=start_date,
            end_date=end_date,
            categories=categories,
            total_trades=0,
            total_markets=total_markets,
            win_rate=0.0,
            avg_return=0.0,
            median_return=0.0,
            total_pnl=0.0,
            total_pnl_with_costs=0.0,
            percentile_5=0.0,
            percentile_25=0.0,
            percentile_75=0.0,
            percentile_95=0.0,
            yes_longshots=0,
            no_longshots=0,
            trades_count=0,
        )

    # Identify longshots
    # YES is longshot if yes_price in [longshot_min, longshot_max]
    yes_longshot = (data["yes_price"] >= longshot_min) & (
        data["yes_price"] <= longshot_max
    )

    # NO is longshot if no_price in [longshot_min, longshot_max]
    no_longshot = (data["no_price"] >= longshot_min) & (
        data["no_price"] <= longshot_max
    )

    # Filter to only longshot opportunities (YES OR NO is longshot)
    longshot_mask = yes_longshot | no_longshot
    trades_data = data[longshot_mask].copy()

    if len(trades_data) == 0:
        # Return empty result
        return pd.DataFrame(), BacktestResult(
            lookback_days=lookback_days,
            longshot_min=longshot_min,
            longshot_max=longshot_max,
            min_volume=min_volume,
            fees_bps=fees_bps,
            slippage_bps=slippage_bps,
            start_date=start_date,
            end_date=end_date,
            categories=categories,
            total_trades=0,
            total_markets=total_markets,
            win_rate=0.0,
            avg_return=0.0,
            median_return=0.0,
            total_pnl=0.0,
            total_pnl_with_costs=0.0,
            percentile_5=0.0,
            percentile_25=0.0,
            percentile_75=0.0,
            percentile_95=0.0,
            yes_longshots=0,
            no_longshots=0,
            trades_count=0,
        )

    # Determine which outcome is longshot and what we bet on
    trades_data["longshot_outcome"] = trades_data.apply(
        lambda row: "YES"
        if (row["yes_price"] >= longshot_min) and (row["yes_price"] <= longshot_max)
        else "NO",
        axis=1,
    )

    trades_data["bet_on_outcome"] = trades_data["longshot_outcome"].apply(
        lambda x: "NO" if x == "YES" else "YES"
    )

    trades_data["entry_price"] = trades_data.apply(
        lambda row: row["no_price"]
        if row["bet_on_outcome"] == "NO"
        else row["yes_price"],
        axis=1,
    )

    # Calculate win/loss
    trades_data["win"] = trades_data["bet_on_outcome"] == trades_data["winner"]

    # Calculate P&L before costs
    trades_data["pnl_gross"] = trades_data.apply(
        lambda row: 1.0 - row["entry_price"] if row["win"] else -row["entry_price"],
        axis=1,
    )

    # Calculate return percentage before costs
    trades_data["return_pct_gross"] = (
        trades_data["pnl_gross"] / trades_data["entry_price"]
    ) * 100

    # Apply fees and slippage
    fees_rate = fees_bps / 10000.0  # Convert basis points to decimal
    slippage_rate = slippage_bps / 10000.0

    # Fees apply to entry (as percentage of entry price)
    # Slippage applies to entry (worsen entry price)
    # Combined effect: reduce P&L by (fees + slippage) * entry_price
    cost_per_trade = (fees_rate + slippage_rate) * trades_data["entry_price"]

    trades_data["pnl_net"] = trades_data["pnl_gross"] - cost_per_trade
    trades_data["return_pct_net"] = (
        trades_data["pnl_net"] / trades_data["entry_price"]
    ) * 100

    # Compute summary statistics
    total_trades = len(trades_data)
    wins = trades_data["win"].sum()
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0

    # Use net returns for statistics
    returns = trades_data["return_pct_net"].values
    returns_sorted = sorted(returns)

    avg_return = returns.mean()
    median_return = returns_sorted[len(returns_sorted) // 2] if returns_sorted else 0.0

    def percentile(data, p):
        if len(data) == 0:
            return 0.0
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data) - 1)]

    p5 = percentile(returns_sorted, 5)
    p25 = percentile(returns_sorted, 25)
    p75 = percentile(returns_sorted, 75)
    p95 = percentile(returns_sorted, 95)

    total_pnl_gross = trades_data["pnl_gross"].sum()
    total_pnl_net = trades_data["pnl_net"].sum()

    yes_longshots = (trades_data["longshot_outcome"] == "YES").sum()
    no_longshots = (trades_data["longshot_outcome"] == "NO").sum()

    # Create result summary
    result = BacktestResult(
        lookback_days=lookback_days,
        longshot_min=longshot_min,
        longshot_max=longshot_max,
        min_volume=min_volume,
        fees_bps=fees_bps,
        slippage_bps=slippage_bps,
        start_date=start_date,
        end_date=end_date,
        categories=categories,
        total_trades=total_trades,
        total_markets=total_markets,
        win_rate=win_rate,
        avg_return=float(avg_return),
        median_return=float(median_return),
        total_pnl=float(total_pnl_gross),
        total_pnl_with_costs=float(total_pnl_net),
        percentile_5=float(p5),
        percentile_25=float(p25),
        percentile_75=float(p75),
        percentile_95=float(p95),
        yes_longshots=int(yes_longshots),
        no_longshots=int(no_longshots),
        trades_count=total_trades,
    )

    # Prepare trades DataFrame for export
    trades_export = trades_data[
        [
            "market_id",
            "question",
            "slug",
            "category",
            "snapshot_time",
            "end_date",
            "longshot_outcome",
            "bet_on_outcome",
            "yes_price",
            "no_price",
            "entry_price",
            "winner",
            "win",
            "pnl_gross",
            "pnl_net",
            "return_pct_gross",
            "return_pct_net",
            "volume",
        ]
    ].copy()

    # Rename end_date to resolution_time for consistency
    trades_export = trades_export.rename(columns={"end_date": "resolution_time"})

    return trades_export, result


def print_backtest_summary(result: BacktestResult) -> None:
    """
    Print human-readable backtest summary.

    Args:
        result: BacktestResult summary
    """
    print("\n" + "=" * 60)
    print("📊 BACKTEST RESULTS")
    print("=" * 60)
    print(f"\nParameters:")
    print(f"  Lookback days:     {result.lookback_days}")
    print(
        f"  Longshot range:    {result.longshot_min*100:.1f}% - {result.longshot_max*100:.1f}%"
    )
    print(f"  Min volume:        ${result.min_volume:,.0f}")
    print(f"  Fees:              {result.fees_bps} bps")
    print(f"  Slippage:          {result.slippage_bps} bps")

    if result.start_date or result.end_date:
        print(f"  Date range:        {result.start_date or 'any'} to {result.end_date or 'any'}")

    if result.categories:
        print(f"  Categories:        {', '.join(result.categories)}")

    print(f"\n{'Total Markets:':<30} {result.total_markets:>10,}")
    print(f"{'Total Trades:':<30} {result.total_trades:>10,}")
    print(f"{'  YES longshots (bought NO):':<30} {result.yes_longshots:>10,}")
    print(f"{'  NO longshots (bought YES):':<30} {result.no_longshots:>10,}")

    print(f"\n{'Win Rate:':<30} {result.win_rate:>9.1f}%")
    print(f"{'Average Return (net):':<30} {result.avg_return:>9.1f}%")
    print(f"{'Median Return (net):':<30} {result.median_return:>9.1f}%")
    print(f"{'Total P&L (gross):':<30} {result.total_pnl:>10.3f}")
    print(f"{'Total P&L (net):':<30} {result.total_pnl_with_costs:>10.3f}")

    print(f"\nReturn Distribution (net):")
    print(f"{'  5th percentile:':<30} {result.percentile_5:>9.1f}%")
    print(f"{'  25th percentile:':<30} {result.percentile_25:>9.1f}%")
    print(f"{'  75th percentile:':<30} {result.percentile_75:>9.1f}%")
    print(f"{'  95th percentile:':<30} {result.percentile_95:>9.1f}%")

    print("\n" + "=" * 60)

    # Interpretation
    if result.total_trades > 0:
        if result.avg_return > 5:
            print("✅ Strategy shows positive edge - longshots appear overvalued")
        elif result.avg_return > 0:
            print("⚠️  Strategy shows slight positive edge - consider more data")
        else:
            print("❌ Strategy shows negative edge - longshots may be undervalued")
    print()
