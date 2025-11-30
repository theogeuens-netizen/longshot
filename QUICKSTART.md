# Quick Start Guide

## What This Does

Tests if betting **against** unlikely outcomes (1-10% probability) on Polymarket is profitable.

## Installation (3 steps)

```bash
# 1. Navigate to project directory
cd path/to/polymarket-longshot-backtest

# 2. Install
pip install -e .

# 3. Run backtest
python -m pm_backtest.cli run-backtest
```

## What Happens Next

The backtest will:
- Download Polymarket data (April-November 2025)
- Find markets where YES or NO had 1-10% probability 7 days before resolution
- Simulate betting against those longshots
- Show you if it was profitable

**First run takes 5-10 minutes** (downloading data). Later runs take ~30 seconds (uses cache).

## Results Location

- **Console**: See summary stats immediately
- **CSV**: `data/processed/trades.csv` (open in Excel)
- **JSON**: `data/processed/summary.json`

## Customize Settings

Edit `config.yaml`:

```yaml
backtest:
  lookback_days: 7          # When to enter (days before resolution)
  longshot_min: 0.01       # Min probability (1%)
  longshot_max: 0.10       # Max probability (10%)
```

Then re-run:
```bash
python -m pm_backtest.cli run-backtest
```

## Need Help?

See [README.md](README.md) for:
- Detailed installation steps
- Troubleshooting guide
- How to interpret results
- Advanced configuration

## Common Issues

**"No markets found"** → Lower `min_liquidity` and `min_volume` in `config.yaml`

**"Rate limited"** → Reduce `max_concurrent_requests` to 1 in `config.yaml`

**"No price data"** → Increase `window_hours` to 48 in `config.yaml`
