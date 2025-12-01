"""
Load data from Dune Analytics (API or CSV) and merge with Gamma cache.
Creates the final backtest database.

Usage examples:

    # 1) Using a Dune query URL (easiest):
    python -m pm_backtest.dune_loader --url "https://dune.com/queries/6284554"

    # 2) Using a numeric query id:
    python -m pm_backtest.dune_loader --query-id 6284554

    # 3) Using a previously downloaded CSV:
    python -m pm_backtest.dune_loader --csv data/dune/dune_prices.csv
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Dune helpers
# ---------------------------------------------------------------------------

def extract_query_id_from_url(url: str) -> int:
    """
    Extract the numeric query id from a Dune query URL.

    Examples:
        https://dune.com/queries/6284554
        https://dune.com/queries/6284554?foo=bar

    Returns:
        int query id

    Raises:
        ValueError if no id can be found.
    """
    m = re.search(r"/queries/(\d+)", url)
    if not m:
        raise ValueError(f"Could not extract query id from URL: {url}")
    return int(m.group(1))


def fetch_from_dune(query_id: int, api_key: Optional[str] = None) -> pd.DataFrame:
    """
    Fetch query results from Dune Analytics API as a pandas DataFrame.

    Assumes the SQL you already ran on Dune returns columns like:
      - condition_id
      - question
      - resolved_on_timestamp
      - outcome
      - price_1h_before, price_24h_before, price_2d_before, etc.
    """
    try:
        from dune_client.client import DuneClient
    except ImportError as e:
        raise ImportError(
            "dune-client not installed. Run:\n\n"
            "   pip install dune-client\n"
        ) from e

    api_key = api_key or os.environ.get("DUNE_API_KEY")
    if not api_key:
        raise ValueError(
            "No API key provided. Set DUNE_API_KEY env var or pass --api-key.\n"
            "Get a free API key at: https://dune.com/settings/api"
        )

    print(f"Connecting to Dune API...")
    dune = DuneClient(api_key)

    print(f"Fetching latest results for query {query_id}...")
    print("(If the query must re-run, this can take a bit.)")

    df = dune.get_latest_result_dataframe(query_id)

    print(f"✓ Got {len(df):,} rows from Dune")
    return _process_dune_df(df)


def load_dune_export(dune_csv_path: str | Path) -> pd.DataFrame:
    """
    Load Dune price export from a local CSV file and standardize columns.
    """
    dune_csv_path = Path(dune_csv_path)
    print(f"Reading Dune CSV: {dune_csv_path}")
    df = pd.read_csv(dune_csv_path)
    return _process_dune_df(df)


def _process_dune_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Process/standardize Dune DataFrame columns to a consistent schema.

    Expected raw columns from your query:
        - condition_id
        - question
        - resolved_on_timestamp
        - outcome (yes/no)
        - price_1h_before, price_24h_before, price_2d_before, price_3d_before,
          price_7d_before, price_14d_before, price_30d_before

    Output columns:
        - condition_id
        - question
        - resolution_time (datetime)
        - winner (YES/NO)
        - yes_price_1h, yes_price_1d, yes_price_2d, ...
        - no_price_1h, no_price_1d, no_price_2d, ...
    """
    df = df.copy()

    rename_map = {
        "resolved_on_timestamp": "resolution_time",
        "outcome": "winner",
        # Map Dune price columns to our naming
        "price_1h_before": "yes_price_1h",
        "price_24h_before": "yes_price_1d",
        "price_2d_before": "yes_price_2d",
        "price_3d_before": "yes_price_3d",
        "price_7d_before": "yes_price_7d",
        "price_14d_before": "yes_price_14d",
        "price_30d_before": "yes_price_30d",
    }

    # Only rename columns that actually exist in df
    rename_map = {k: v for k, v in rename_map.items() if k in df.columns}
    df = df.rename(columns=rename_map)

    # Normalize winner (YES/NO)
    if "winner" in df.columns:
        df["winner"] = df["winner"].astype(str).str.strip().str.upper()

    # Parse resolution_time
    if "resolution_time" in df.columns and df["resolution_time"].dtype == "object":
        df["resolution_time"] = pd.to_datetime(df["resolution_time"])

    # Compute NO prices = 1 - YES price
    yes_cols = [c for c in df.columns if c.startswith("yes_price_")]
    for col in yes_cols:
        lookback = col.replace("yes_price_", "")
        df[f"no_price_{lookback}"] = 1 - df[col]

    return df


# ---------------------------------------------------------------------------
# Gamma cache
# ---------------------------------------------------------------------------

def load_gamma_cache(data_dir: str | Path = "data/raw") -> pd.DataFrame:
    """
    Load the most recent Gamma API cache.

    Expects files like: data/raw/markets_*.json with fields:
        - conditionId
        - slug
        - category
        - volumeNum / volume
        - liquidityNum / liquidity
        - endDateIso / endDate
    """
    data_dir = Path(data_dir)
    cache_files = sorted(data_dir.glob("markets_*.json"))

    if not cache_files:
        raise FileNotFoundError(f"No Gamma cache found in {data_dir}")

    latest = cache_files[-1]
    print(f"Loading Gamma cache: {latest}")

    with open(latest, "r") as f:
        raw_markets = json.load(f)

    gamma_data = []
    for m in raw_markets:
        gamma_data.append(
            {
                "condition_id": m.get("conditionId"),
                "slug": m.get("slug"),
                "category": m.get("category"),
                "volume": m.get("volumeNum") or m.get("volume") or 0,
                "liquidity": m.get("liquidityNum") or m.get("liquidity") or 0,
                "end_date": m.get("endDateIso") or m.get("endDate"),
            }
        )

    return pd.DataFrame(gamma_data)


def merge_dune_gamma(dune_df: pd.DataFrame, gamma_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge Dune prices with Gamma metadata on condition_id (case-insensitive).
    """
    dune_df = dune_df.copy()
    gamma_df = gamma_df.copy()

    dune_df["condition_id"] = dune_df["condition_id"].astype(str).str.lower()
    gamma_df["condition_id"] = gamma_df["condition_id"].astype(str).str.lower()

    merged = dune_df.merge(
        gamma_df[["condition_id", "slug", "category", "volume", "liquidity"]],
        on="condition_id",
        how="left",
    )
    return merged


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def build_backtest_database(
    query_id: Optional[int] = None,
    query_url: Optional[str] = None,
    dune_csv_path: str | Path | None = None,
    api_key: Optional[str] = None,
    gamma_dir: str | Path = "data/raw",
    output_path: str | Path = "data/db/backtest_db.parquet",
) -> pd.DataFrame:
    """
    Build the complete backtest database.

    You must provide ONE of:
        - query_url  (preferred)
        - query_id
        - dune_csv_path

    Args:
        query_id: Dune query id (e.g., 6284554)
        query_url: Full Dune query URL (e.g., https://dune.com/queries/6284554)
        dune_csv_path: Path to local CSV export
        api_key: Dune API key (or via DUNE_API_KEY env var)
        gamma_dir: Directory with Gamma cache JSON files
        output_path: Where to save the final parquet

    Returns:
        Merged DataFrame.
    """
    # Resolve query_id or CSV input
    if query_url:
        query_id = extract_query_id_from_url(query_url)
    if not query_id and not dune_csv_path:
        raise ValueError("Provide one of: query_url, query_id, or dune_csv_path")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Building Backtest Database")
    print("=" * 60)

    # 1) Load Dune data
    print("\n[1/3] Loading Dune data...")
    if dune_csv_path:
        dune_df = load_dune_export(dune_csv_path)
    else:
        dune_df = fetch_from_dune(query_id=query_id, api_key=api_key)

    print(f"      {len(dune_df):,} markets with prices")
    if "resolution_time" in dune_df.columns:
        print(
            f"      Date range: {dune_df['resolution_time'].min()} "
            f"→ {dune_df['resolution_time'].max()}"
        )

    # 2) Load Gamma cache
    print("\n[2/3] Loading Gamma cache...")
    gamma_df = load_gamma_cache(gamma_dir)
    print(f"      {len(gamma_df):,} markets in cache")

    # 3) Merge
    print("\n[3/3] Merging datasets...")
    merged = merge_dune_gamma(dune_df, gamma_df)
    matched = merged["category"].notna().sum()
    print(f"      {matched:,}/{len(merged):,} markets matched with Gamma metadata")

    # Save
    merged.to_parquet(output_path, index=False)
    print(f"\n✓ Saved to {output_path}")

    _print_summary(merged)
    return merged


def _print_summary(df: pd.DataFrame) -> None:
    """Print high-level summary statistics for the database."""
    print("\n" + "=" * 60)
    print("DATABASE SUMMARY")
    print("=" * 60)

    print(f"\nTotal markets: {len(df):,}")

    if "winner" in df.columns:
        print("\nBy winner:")
        print(df["winner"].value_counts().to_string())

    if "category" in df.columns and df["category"].notna().any():
        print("\nBy category (top 10):")
        print(df["category"].value_counts().head(10).to_string())
    else:
        print("\nBy category: (no category data matched)")

    print("\nPrice coverage:")
    price_cols = sorted([c for c in df.columns if c.startswith("yes_price_")])
    for col in price_cols:
        coverage = df[col].notna().sum()
        pct = 100 * coverage / len(df)
        print(f"  {col}: {coverage:,}/{len(df):,} ({pct:.1f}%)")

    if "volume" in df.columns:
        vol = df[df["volume"] > 0]["volume"]
        if len(vol) > 0:
            print("\nVolume stats:")
            print(f"  Markets with volume: {len(vol):,}")
            print(f"  Median: ${vol.median():,.0f}")
            print(f"  Mean:   ${vol.mean():,.0f}")
            print(f"  Max:    ${vol.max():,.0f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Build backtest database from Dune + Gamma data"
    )
    parser.add_argument(
        "--url",
        type=str,
        help="Full Dune query URL (e.g. https://dune.com/queries/6284554)",
    )
    parser.add_argument(
        "--query-id",
        "-q",
        type=int,
        help="Dune query ID to fetch (e.g., 6284554)",
    )
    parser.add_argument(
        "--csv",
        "-c",
        type=str,
        help="Path to local Dune CSV export (alternative to --url/--query-id)",
    )
    parser.add_argument(
        "--api-key",
        "-k",
        type=str,
        help="Dune API key (or set DUNE_API_KEY env var)",
    )
    parser.add_argument(
        "--gamma-dir",
        "-g",
        type=str,
        default="data/raw",
        help="Directory with Gamma cache JSON files (default: data/raw)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="data/db/backtest_db.parquet",
        help="Output parquet path (default: data/db/backtest_db.parquet)",
    )

    args = parser.parse_args()

    if not args.url and not args.query_id and not args.csv:
        parser.error("Provide one of: --url, --query-id, or --csv")

    build_backtest_database(
        query_url=args.url,
        query_id=args.query_id,
        dune_csv_path=args.csv,
        api_key=args.api_key,
        gamma_dir=args.gamma_dir,
        output_path=args.output,
    )
