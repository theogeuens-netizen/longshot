# Polymarket Longshot Bias Backtest

A backtesting system to test the **longshot bias** hypothesis on Polymarket binary markets.

## Strategy

The longshot bias hypothesis suggests that outcomes with very low probabilities (1-10%) tend to be **overvalued** by bettors due to lottery-ticket appeal or noise.

This backtest:
1. Scans historical resolved Polymarket binary (YES/NO) markets
2. At 7 days before resolution, identifies "longshots" - outcomes with 1-10% implied probability
3. Simulates betting **against** the longshot (if YES is 5%, buy NO; if NO is 5%, buy YES)
4. Holds to resolution and measures profitability

## Installation

### Prerequisites
- Python 3.11 or higher
- pip

### Setup

1. **Clone this repository** (or navigate to this directory)

2. **Install dependencies**:
```bash
pip install -e .
```

3. **Configure the backtest** (optional):
   - Edit `config.yaml` to adjust strategy parameters
   - Default: 7-day lookback, 1-10% longshot range, Apr-Nov 2025 data

## Usage

### Run the backtest

```bash
python -m pm_backtest.cli run-backtest
```

This will:
- Fetch resolved markets from Polymarket (Apr-Nov 2025)
- Get price snapshots 7 days before resolution
- Identify longshot opportunities
- Simulate trades
- Output results to `data/processed/`

### Output

- **Console**: Summary statistics (win rate, avg return, etc.)
- **CSV**: `data/processed/trades.csv` - detailed per-trade results
- **JSON**: `data/processed/summary.json` - aggregate metrics

## Configuration

Key parameters in `config.yaml`:

- `lookback_days`: Days before resolution to snapshot prices (default: 7)
- `longshot_min/max`: Price range to qualify as longshot (default: 1-10%)
- `min_liquidity/volume`: Filter out low-quality markets
- `use_cache`: Use cached API responses to avoid re-fetching (default: true)

## Project Structure

```
polymarket-longshot-backtest/
├── config.yaml              # Strategy and API configuration
├── pyproject.toml           # Python dependencies
├── src/pm_backtest/         # Main package
│   ├── cli.py              # Command-line interface
│   ├── config.py           # Config loader
│   ├── models.py           # Data models
│   ├── gamma_client.py     # Polymarket Gamma API client
│   ├── clob_client.py      # Polymarket CLOB API client
│   └── longshot_logic.py   # Backtest logic
└── data/
    ├── raw/                # Cached API responses
    └── processed/          # Results (CSV, JSON)
```

## Data Sources

- **Polymarket Gamma API**: Market metadata and resolution data
  - Docs: https://docs.polymarket.com/api-reference/markets/list-markets
- **Polymarket CLOB API**: Historical price data
  - Docs: https://docs.polymarket.com/api-reference/pricing/get-price-history-for-a-traded-token

## Limitations

- Currently ignores trading fees (placeholder added for future implementation)
- Assumes perfect execution at snapshot price
- Does not account for slippage or market impact

## License

MIT
