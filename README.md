# Polymarket Longshot Bias Backtest

A backtesting system to test the **longshot bias** hypothesis on Polymarket binary markets.

## Strategy

The longshot bias hypothesis suggests that outcomes with very low probabilities (1-10%) tend to be **overvalued** by bettors due to lottery-ticket appeal or noise.

This backtest:
1. Scans historical resolved Polymarket binary (YES/NO) markets
2. At 7 days before resolution, identifies "longshots" - outcomes with 1-10% implied probability
3. Simulates betting **against** the longshot (if YES is 5%, buy NO; if NO is 5%, buy YES)
4. Holds to resolution and measures profitability

## Quick Start (For Non-Technical Users)

### Prerequisites
- Python 3.11 or higher ([Download here](https://www.python.org/downloads/))
- Basic terminal/command line access

### Step-by-Step Installation

1. **Open your terminal** (Command Prompt on Windows, Terminal on Mac/Linux)

2. **Navigate to this project directory**:
   ```bash
   cd path/to/polymarket-longshot-backtest
   ```

3. **Install the project** (this installs all dependencies automatically):
   ```bash
   pip install -e .
   ```

   This may take a minute as it downloads required libraries.

4. **Verify installation**:
   ```bash
   python -m pm_backtest.cli --help
   ```

   You should see a help message with available commands.

### Running Your First Backtest

Simply run:
```bash
python -m pm_backtest.cli run-backtest
```

The backtest will:
1. Fetch resolved markets from Polymarket (April-November 2025)
2. Get price snapshots 7 days before each market resolved
3. Identify "longshot" opportunities (1-10% probability outcomes)
4. Simulate betting against these longshots
5. Calculate profitability and statistics
6. Save results to `data/processed/`

**First run will be slower** as it downloads data from Polymarket's API. Subsequent runs use cached data and are much faster!

### Understanding the Output

After the backtest completes, you'll see:

**Console Output:**
```
📊 BACKTEST RESULTS
===========================================================
Total Trades:                    156
  YES longshots (bought NO):      89
  NO longshots (bought YES):      67

Win Rate:                       62.8%
Average Return:                  8.3%
Median Return:                   7.1%
Total P&L:                      12.948
```

**Files Created:**
- `data/processed/trades.csv` - Detailed results for each trade (open in Excel)
- `data/processed/trades.parquet` - Same data in efficient binary format
- `data/processed/summary.json` - Summary statistics in JSON format

### Customizing the Backtest

Edit `config.yaml` to adjust parameters:

```yaml
backtest:
  lookback_days: 7          # Change to 3, 14, etc. for different entry times
  longshot_min: 0.01       # Minimum probability (1%)
  longshot_max: 0.10       # Maximum probability (10%)
  start_end_date: "2025-04-01T00:00:00Z"  # Start of date range
  end_end_date: "2025-11-01T00:00:00Z"    # End of date range
```

After editing, re-run:
```bash
python -m pm_backtest.cli run-backtest
```

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

## Advanced Configuration

### API Rate Limiting

The backtest is designed to be respectful of Polymarket's API:

```yaml
api:
  max_concurrent_requests: 3    # Concurrent API calls (reduce if rate limited)
  base_sleep_seconds: 0.2       # Delay between requests
  request_timeout_seconds: 15   # Timeout for each request
  max_retries: 5                # Retry attempts for failed requests
  use_cache: true               # Cache API responses (recommended!)
```

### Caching

- First run: Downloads all data fresh from API (slow, ~5-10 minutes)
- Subsequent runs: Uses cached data from `data/raw/` (fast, ~30 seconds)
- To force fresh data: Set `use_cache: false` in config or delete `data/raw/`

## Troubleshooting

### "No markets match the filter criteria"

**Problem:** The backtest found no markets in your date range or liquidity filters are too strict.

**Solutions:**
- Check your `start_end_date` and `end_end_date` in `config.yaml`
- Lower `min_liquidity` and `min_volume` (try 100 and 500 respectively)
- Verify Polymarket had active markets during your chosen period

### "No valid price snapshots found"

**Problem:** Markets were found but price data is missing.

**Solutions:**
- Increase `window_hours` in config (try 48 or 72)
- Reduce `lookback_days` (try 3 or 5 instead of 7)
- Some markets may not have early price history - this is normal

### "Rate limited by API"

**Problem:** Too many requests to Polymarket API.

**Solutions:**
- Reduce `max_concurrent_requests` to 1 or 2
- Increase `base_sleep_seconds` to 0.5 or 1.0
- Wait a few minutes and try again
- Ensure `use_cache: true` to avoid redundant requests

### "Timeout fetching markets/prices"

**Problem:** API requests are timing out.

**Solutions:**
- Increase `request_timeout_seconds` to 30 or 60
- Check your internet connection
- Try again later - API might be slow

## Understanding the Results

### Interpreting Returns

- **Win Rate**: Percentage of trades that won (your bet matched the outcome)
- **Average Return**: Mean return per trade (positive = profitable strategy)
- **Median Return**: Middle value (more robust to outliers than average)
- **Total P&L**: Sum of all profits/losses (assumes $1 per trade)

### What Does a Good Result Look Like?

- **Positive edge**: Average return > 0% consistently
- **Statistical significance**: Need 50+ trades for reliable conclusions
- **Risk-adjusted**: Check percentile distribution for tail risks

### Example Interpretation

```
Win Rate: 62.8%
Average Return: 8.3%
```

This means:
- 62.8% of longshot bets won (we bet against longshots)
- On average, each $1 bet returned $1.083 (8.3% profit)
- Longshots appear to be overvalued - betting against them is profitable

## Limitations

- **No fees**: Currently ignores trading fees (placeholder added for future implementation)
- **Perfect execution**: Assumes you can trade at exact snapshot price
- **No slippage**: Doesn't account for market impact or spread
- **Survivor bias**: Only analyzes resolved markets (excludes abandoned/invalid ones)
- **Historical data only**: Past performance doesn't guarantee future results

## Extending This Project

Want to improve the backtest? Ideas:

1. **Add fees**: Modify P&L calculation in `longshot_logic.py` to subtract trading fees
2. **Different entry times**: Test 1, 3, 14, 30 days before resolution
3. **Dynamic thresholds**: Vary longshot range by market liquidity
4. **Kelly criterion**: Optimize bet sizing based on edge and variance
5. **Market filters**: Test different categories (politics, sports, crypto)

## Data Sources

- **Polymarket Gamma API**: Market metadata and resolution data
  - Docs: https://docs.polymarket.com/api-reference/markets/list-markets
- **Polymarket CLOB API**: Historical price data
  - Docs: https://docs.polymarket.com/api-reference/pricing/get-price-history-for-a-traded-token

## Support

If you encounter issues:

1. Check this README's Troubleshooting section
2. Verify your `config.yaml` is valid
3. Try with `use_cache: false` to fetch fresh data
4. Check Polymarket API status

## License

MIT
