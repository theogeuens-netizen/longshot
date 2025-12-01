"""
Simple Dune + Gamma backtest database builder.

- Uses DuneClient("$API_KEY") style (like the docs snippet).
- Expects your Dune SQL to return:
    condition_id, question, resolved_on_timestamp, outcome,
    price_1h_before, price_24h_before, price_2d_before,
    price_3d_before, price_7d_before, price_14d_before, price_30d_before
- Expects a Gamma cache JSON: markets_*.json

Usage (Python):

    from pm_backtest.dune_loader import build_backtest_database

    df = build_backtest_database(
        query_id=6284837,
        api_key="YOUR_API_KEY_HERE",
        gamma_dir="data/raw",
        output_path="data/db/backtest_db.parquet",
    )
"""

import json
from pathlib import Path
from typing import Optional

import pandas as pd
from dune_client.client import DuneClient


# ---------------------------------------------------------------------
# Dune side
# ---------------------------------------------------------------------

def fetch_from_dune(query_id: int, api_key: str) -> pd.DataFrame:
    """
    Fetch query results from Dune Analytics as a DataFrame.

    This matches the docs style:

        from dune_client.client import DuneClient
        dune = DuneClient("$API_KEY")
        dune.get_latest_result(query_id)

    but we immediately use get_latest_result_dataframe for pandas.
    """
    dune = DuneClient(api_key)
    print(f"Fetching latest result for Dune query {query_id}...")
    df = dune.get_latest_result_dataframe(query_id)
    print(f"✓ Got {len(df):,} rows from Dune")
    return _process_dune_df(df)


def _process_dune_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize Dune output columns.

    Input columns (from your SQL):
        - condition_id
        - question
        - resolved_on_timestamp
        - outcome ('yes'/'no')
        - price_1h_before, price_24h_before, price_2d_before,
          price_3d_before, price_7d_before, price_14d_before, price_30d_before

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
        "price_1h_before": "yes_price_1h",
        "price_24h_before": "yes_price_1d",
        "price_2d_before": "yes_price_2d",
        "price_3d_before": "yes_price_3d",
        "price_7d_before": "yes_price_7d",
        "price_14d_before": "yes_price_14d",
        "price_30d_before": "yes_price_30d",
    }
    rename_map = {k: v for k, v in rename_map.items() if k in df.columns}
    df = df.rename(columns=rename_map)

    # winner → YES/NO
    if "winner" in df.columns:
        df["winner"] = df["winner"].astype(str).str.strip().str.upper()

    # parse resolution_time
    if "resolution_time" in df.columns and df["resolution_time"].dtype == "object":
        df["resolution_time"] = pd.to_datetime(df["resolution_time"])

    # NO prices = 1 - YES price
    yes_cols = [c for c in df.columns if c.startswith("yes_price_")]
    for col in yes_cols:
        lookback = col.replace("yes_price_", "")
        df[f"no_price_{lookback}"] = 1 - df[col]

    return df


# ---------------------------------------------------------------------
# Gamma side
# ---------------------------------------------------------------------

def load_gamma_cache(data_dir: str | Path = "data/raw") -> pd.DataFrame:
    """
    Load the most recent Gamma API cache (markets_*.json).
    """
    data_dir = Path(data_dir)
    cache_files = sorted(data_dir.glob("markets_*.json"))
    if not cache_files:
        raise FileNotFoundError(f"No Gamma cache found in {data_dir}")

    latest = cache_files[-1]
    print(f"Loading Gamma cache: {latest}")
    with open(latest, "r") as f:
        raw_markets = json.load(f)

    gamma_rows = []
    for m in raw_markets:
        gamma_rows.append(
            {
                "condition_id": m.get("conditionId"),
                "slug": m.get("slug"),
                "category": m.get("category"),
                "volume": m.get("volumeNum") or m.get("volume") or 0,
                "liquidity": m.get("liquidityNum") or m.get("liquidity") or 0,
                "end_date": m.get("endDateIso") or m.get("endDate"),
            }
        )

    return pd.DataFrame(gamma_rows)


def merge_dune_gamma(dune_df: pd.DataFrame, gamma_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge Dune prices with Gamma metadata on condition_id (case-insensitive).
    """
    dune_df = dune_df.copy()
    gamma_df = gamma_df.copy()

    dune_df["condition_id"] = dune_df["condition_id"].astype(str).str.lower()
    gamma_df["condition_id"] = gamma_df["condition_id"].astype(str).str.lower()

    merged = dune_df.merge(
        ga
