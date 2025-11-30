"""Configuration loader and validator."""

import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import BacktestConfig


def load_config(config_path: str = "config.yaml") -> BacktestConfig:
    """
    Load and validate configuration from YAML file.

    Args:
        config_path: Path to config.yaml file

    Returns:
        Validated BacktestConfig object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid
    """
    config_file = Path(config_path)

    if not config_file.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            f"Please create a config.yaml file in the project root.\n"
            f"See README.md for configuration options."
        )

    try:
        with open(config_file) as f:
            raw_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse YAML config: {e}")

    # Flatten nested structure for Pydantic model
    flattened = _flatten_config(raw_config)

    try:
        config = BacktestConfig(**flattened)
    except ValidationError as e:
        print("\n❌ Configuration validation failed:\n", file=sys.stderr)
        for error in e.errors():
            field = ".".join(str(x) for x in error["loc"])
            msg = error["msg"]
            print(f"  • {field}: {msg}", file=sys.stderr)
        print("\nPlease check your config.yaml file.\n", file=sys.stderr)
        raise ValueError("Invalid configuration")

    # Validate logical constraints
    _validate_config_logic(config)

    return config


def _flatten_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested config structure for Pydantic model."""
    flattened = {
        "gamma_base_url": raw.get("gamma_base_url", ""),
        "clob_base_url": raw.get("clob_base_url", ""),
    }

    # Backtest section
    if "backtest" in raw:
        bt = raw["backtest"]
        flattened.update(
            {
                "lookback_days": bt.get("lookback_days", 7),
                "window_hours": bt.get("window_hours", 24),
                "longshot_min": bt.get("longshot_min", 0.01),
                "longshot_max": bt.get("longshot_max", 0.10),
                "min_liquidity": bt.get("min_liquidity", 1000),
                "min_volume": bt.get("min_volume", 5000),
                "start_end_date": bt.get("start_end_date", "2025-04-01T00:00:00Z"),
                "end_end_date": bt.get("end_end_date", "2025-11-01T00:00:00Z"),
            }
        )

    # API section
    if "api" in raw:
        api = raw["api"]
        flattened.update(
            {
                "max_concurrent_requests": api.get("max_concurrent_requests", 3),
                "request_timeout_seconds": api.get("request_timeout_seconds", 15),
                "base_sleep_seconds": api.get("base_sleep_seconds", 0.2),
                "max_retries": api.get("max_retries", 5),
                "use_cache": api.get("use_cache", True),
                "gamma_page_limit": api.get("gamma_page_limit", 100),
            }
        )

    return flattened


def _validate_config_logic(config: BacktestConfig) -> None:
    """Validate logical constraints in config."""
    if config.longshot_min >= config.longshot_max:
        raise ValueError(
            f"longshot_min ({config.longshot_min}) must be < longshot_max ({config.longshot_max})"
        )

    if config.longshot_min < 0 or config.longshot_max > 1:
        raise ValueError(
            f"Longshot thresholds must be between 0 and 1 "
            f"(got min={config.longshot_min}, max={config.longshot_max})"
        )

    if config.start_end_date >= config.end_end_date:
        raise ValueError(
            f"start_end_date ({config.start_end_date}) must be before "
            f"end_end_date ({config.end_end_date})"
        )

    if config.lookback_days < 1:
        raise ValueError(f"lookback_days must be >= 1 (got {config.lookback_days})")

    if config.max_concurrent_requests < 1:
        raise ValueError(
            f"max_concurrent_requests must be >= 1 (got {config.max_concurrent_requests})"
        )

    print("✓ Configuration loaded successfully")
    print(f"  Backtest period: {config.start_end_date.date()} to {config.end_end_date.date()}")
    print(
        f"  Longshot range: {config.longshot_min*100:.1f}% - {config.longshot_max*100:.1f}%"
    )
    print(f"  Lookback: {config.lookback_days} days before resolution")
    print()
