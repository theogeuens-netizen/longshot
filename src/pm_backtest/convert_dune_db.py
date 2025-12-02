"""
Convert Dune+Gamma merged database to the two-file format expected by the backtester.

The dune_loader.py creates a single "wide" format file:
    data/db/backtest_db.parquet (one row per market, prices as columns)

The backtester expects two "normalized" files:
    data/db/markets.parquet   (market metadata)
    data/db/snapshots.parquet (prices in long format, one row per market+lookback)

This script converts between the two formats.

Usage:
    python -m pm_backtest.convert_dune_db
    python -m pm_backtest.convert_dune_db --input data/db/backtest_db.parquet
    python -m pm_backtest.convert_dune_db --input data/db/backtest_db.parquet --output-dir data/db
"""

import argparse
from pathlib import Path

import pandas as pd


# Mapping from Dune column suffix to lookback_days
LOOKBACK_MAP = {
    "1h": None,   # Skip - not a full day
    "1d": 1,
    "2d": 2,
    "3d": 3,
    "7d": 7,
    "14d": 14,
    "30d": 30,
}


def convert_dune_to_backtest_format(
    input_path: str = "data/db/backtest_db.parquet",
    output_dir: str = "data/db",
) -> tuple[Path, Path]:
    """
    Convert Dune+Gamma merged parquet to markets + snapshots format.

    Args:
        input_path: Path to the merged Dune parquet file
        output_dir: Directory to write markets.parquet and snapshots.parquet

    Returns:
        Tuple of (markets_path, snapshots_path)
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("🔄 CONVERTING DUNE DATABASE TO BACKTEST FORMAT")
    print("=" * 60)
    print(f"\nInput:  {input_path}")
    print(f"Output: {output_dir}/\n")

    # Load merged Dune data
    if not input_path.exists():
        raise FileNotFoundError(
            f"Input file not found: {input_path}\n"
            f"Run dune_loader.py first to create this file."
        )

    print("📂 Loading Dune database...")
    df = pd.read_parquet(input_path)
    print(f"   ✓ Loaded {len(df):,} markets\n")

    # Show available columns
    print("📋 Available columns:")
    for col in sorted(df.columns):
        print(f"   • {col}")
    print()

    # === Step 1: Create markets.parquet ===
    print("🏪 Creating markets.parquet...")

    # Identify the market ID column (could be 'condition_id' or 'market_id')
    if "condition_id" in df.columns:
        id_col = "condition_id"
    elif "market_id" in df.columns:
        id_col = "market_id"
    else:
        raise ValueError("No market ID column found (expected 'condition_id' or 'market_id')")

    # Identify the end date column
    if "resolution_time" in df.columns:
        date_col = "resolution_time"
    elif "end_date" in df.columns:
        date_col = "end_date"
    else:
        raise ValueError("No date column found (expected 'resolution_time' or 'end_date')")

    # Build markets dataframe
    market_cols = [id_col]

    # Add optional columns if they exist
    optional_cols = ["question", "slug", "category", "volume", "liquidity", "winner"]
    for col in optional_cols:
        if col in df.columns:
            market_cols.append(col)

    market_cols.append(date_col)

    markets_df = df[market_cols].copy()

    # Standardize column names
    markets_df = markets_df.rename(columns={
        id_col: "market_id",
        date_col: "end_date",
    })

    # Ensure end_date is datetime
    if markets_df["end_date"].dtype == "object":
        markets_df["end_date"] = pd.to_datetime(markets_df["end_date"])

    # Ensure winner is uppercase
    if "winner" in markets_df.columns:
        markets_df["winner"] = markets_df["winner"].astype(str).str.strip().str.upper()
        # Replace 'NAN' string with actual NaN
        markets_df.loc[markets_df["winner"] == "NAN", "winner"] = None

    # Fill missing values
    if "volume" not in markets_df.columns:
        markets_df["volume"] = 0.0
    if "liquidity" not in markets_df.columns:
        markets_df["liquidity"] = 0.0
    if "category" not in markets_df.columns:
        markets_df["category"] = None
    if "slug" not in markets_df.columns:
        markets_df["slug"] = ""
    if "question" not in markets_df.columns:
        markets_df["question"] = ""

    # Reorder columns
    markets_df = markets_df[[
        "market_id", "question", "slug", "category",
        "end_date", "volume", "liquidity", "winner"
    ]]

    print(f"   ✓ {len(markets_df):,} markets")

    # === Step 2: Create snapshots.parquet ===
    print("📸 Creating snapshots.parquet...")

    # Find all yes_price columns
    yes_price_cols = [c for c in df.columns if c.startswith("yes_price_")]
    print(f"   Found price columns: {yes_price_cols}")

    snapshots = []

    for _, row in df.iterrows():
        market_id = row[id_col]
        end_date = pd.to_datetime(row[date_col])

        for yes_col in yes_price_cols:
            # Extract lookback suffix (e.g., "7d" from "yes_price_7d")
            suffix = yes_col.replace("yes_price_", "")

            # Get lookback days
            lookback_days = LOOKBACK_MAP.get(suffix)
            if lookback_days is None:
                continue  # Skip non-day lookbacks like "1h"

            # Get prices
            yes_price = row.get(yes_col)
            no_col = f"no_price_{suffix}"
            no_price = row.get(no_col)

            # Skip if prices are missing
            if pd.isna(yes_price) or pd.isna(no_price):
                continue

            # Calculate snapshot time
            snapshot_time = end_date - pd.Timedelta(days=lookback_days)

            snapshots.append({
                "market_id": market_id,
                "lookback_days": lookback_days,
                "snapshot_time": snapshot_time,
                "yes_price": float(yes_price),
                "no_price": float(no_price),
            })

    snapshots_df = pd.DataFrame(snapshots)

    if snapshots_df.empty:
        print("   ⚠️  No valid snapshots found!")
        print("   Check that your Dune query returns yes_price_* columns")
    else:
        print(f"   ✓ {len(snapshots_df):,} snapshots")

        # Show distribution
        print("\n   Snapshots by lookback period:")
        for days, count in snapshots_df["lookback_days"].value_counts().sort_index().items():
            print(f"      {days:2d} days: {count:,} snapshots")

    # === Step 3: Save to parquet ===
    print("\n💾 Saving parquet files...")

    markets_path = output_dir / "markets.parquet"
    snapshots_path = output_dir / "snapshots.parquet"

    markets_df.to_parquet(markets_path, index=False)
    print(f"   ✓ Saved {markets_path}")

    snapshots_df.to_parquet(snapshots_path, index=False)
    print(f"   ✓ Saved {snapshots_path}")

    # === Summary ===
    print("\n" + "=" * 60)
    print("✅ CONVERSION COMPLETE")
    print("=" * 60)
    print(f"\nCreated two files linked by 'market_id':\n")
    print(f"  📁 {markets_path}")
    print(f"     └── {len(markets_df):,} markets (metadata + winner)")
    print(f"\n  📁 {snapshots_path}")
    print(f"     └── {len(snapshots_df):,} snapshots (prices at different lookbacks)")

    print("\n🎯 Next step - run a backtest:")
    print("   python -m pm_backtest.cli backtest --lookback 7")
    print()

    return markets_path, snapshots_path


def main():
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Convert Dune+Gamma merged database to backtest format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default paths
  python -m pm_backtest.convert_dune_db

  # Custom input path
  python -m pm_backtest.convert_dune_db --input data/db/my_dune_data.parquet

  # Custom output directory
  python -m pm_backtest.convert_dune_db --output-dir data/my_db
        """,
    )
    parser.add_argument(
        "--input", "-i",
        default="data/db/backtest_db.parquet",
        help="Path to Dune+Gamma merged parquet file (default: data/db/backtest_db.parquet)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="data/db",
        help="Directory to write markets.parquet and snapshots.parquet (default: data/db)",
    )

    args = parser.parse_args()

    try:
        convert_dune_to_backtest_format(args.input, args.output_dir)
    except FileNotFoundError as e:
        print(f"\n❌ {e}\n")
        exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
