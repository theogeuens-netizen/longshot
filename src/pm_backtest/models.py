"""Data models for Polymarket backtest."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Outcome(str, Enum):
    """Binary outcome type."""

    YES = "YES"
    NO = "NO"


class Market(BaseModel):
    """Polymarket market from Gamma API."""

    id: str
    question: str
    slug: str
    outcomes: list[str]  # Raw outcomes from API (e.g., ["Yes", "No"])
    outcome_prices: list[float]  # Parsed from outcomePrices JSON string
    clob_token_ids: list[str]  # Parsed from clobTokenIds JSON string
    end_date: datetime  # Market resolution time
    start_date: Optional[datetime] = None
    liquidity: float = Field(alias="liquidityNum", default=0.0)
    volume: float = Field(alias="volumeNum", default=0.0)
    closed: bool = False
    resolved: bool = False  # Inferred from outcome_prices

    @field_validator("end_date", "start_date", mode="before")
    @classmethod
    def parse_datetime(cls, v):
        """Parse datetime from ISO string."""
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        return datetime.fromisoformat(v.replace("Z", "+00:00"))

    def get_yes_index(self) -> Optional[int]:
        """Get index of YES outcome in outcomes array."""
        for i, outcome in enumerate(self.outcomes):
            if outcome.lower() == "yes":
                return i
        return None

    def get_no_index(self) -> Optional[int]:
        """Get index of NO outcome in outcomes array."""
        for i, outcome in enumerate(self.outcomes):
            if outcome.lower() == "no":
                return i
        return None

    def get_winner(self) -> Optional[Outcome]:
        """Infer winner from outcome_prices (1.0 = winner, 0.0 = loser)."""
        if not self.outcome_prices or len(self.outcome_prices) != 2:
            return None

        # Find which outcome has price closest to 1.0
        yes_idx = self.get_yes_index()
        no_idx = self.get_no_index()

        if yes_idx is None or no_idx is None:
            return None

        yes_price = self.outcome_prices[yes_idx]
        no_price = self.outcome_prices[no_idx]

        # Winner should be very close to 1.0 (>0.95)
        if yes_price > 0.95:
            return Outcome.YES
        elif no_price > 0.95:
            return Outcome.NO
        else:
            return None  # Unresolved or invalid

    def is_binary_yes_no(self) -> bool:
        """Check if this is a binary YES/NO market."""
        if len(self.outcomes) != 2:
            return False

        outcomes_lower = {o.lower() for o in self.outcomes}
        return outcomes_lower == {"yes", "no"}


class PricePoint(BaseModel):
    """Price data point from CLOB API."""

    timestamp: datetime = Field(alias="t")
    price: float = Field(alias="p")

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v):
        """Parse timestamp from UNIX seconds."""
        if isinstance(v, datetime):
            return v
        return datetime.fromtimestamp(int(v), tz=timezone.utc)


class Snapshot(BaseModel):
    """Price snapshot at lookback time before resolution."""

    market_id: str
    question: str
    snapshot_time: datetime
    resolution_time: datetime

    # Prices at snapshot
    yes_price: float
    no_price: float

    # Which outcome (if any) is a longshot at snapshot
    longshot_outcome: Optional[Outcome] = None

    # Entry details if we took a trade
    entered_trade: bool = False
    bet_on_outcome: Optional[Outcome] = None  # What we bought
    entry_price: float = 0.0  # Price we paid

    # Market metadata
    liquidity: float = 0.0
    volume: float = 0.0


class TradeResult(BaseModel):
    """Result of a simulated trade."""

    market_id: str
    question: str
    snapshot_time: datetime
    resolution_time: datetime

    # Entry
    longshot_outcome: Outcome  # The longshot we bet against
    bet_on_outcome: Outcome  # What we actually bought
    entry_price: float

    # Exit
    winner: Outcome
    win: bool  # Did our bet win?
    pnl: float  # Profit/loss per unit stake
    return_pct: float  # Return as percentage

    # Market metadata
    liquidity: float
    volume: float
    slug: str = ""


class BacktestConfig(BaseModel):
    """Backtest configuration."""

    # API
    gamma_base_url: str
    clob_base_url: str

    # Strategy
    lookback_days: int
    window_hours: int
    longshot_min: float
    longshot_max: float
    min_liquidity: float
    min_volume: float
    start_end_date: datetime
    end_end_date: datetime

    # API settings
    max_concurrent_requests: int
    request_timeout_seconds: int
    base_sleep_seconds: float
    max_retries: int
    use_cache: bool
    gamma_page_limit: int

    @field_validator("start_end_date", "end_end_date", mode="before")
    @classmethod
    def parse_datetime(cls, v):
        """Parse datetime from ISO string."""
        if isinstance(v, datetime):
            return v
        return datetime.fromisoformat(v.replace("Z", "+00:00"))


class BacktestSummary(BaseModel):
    """Summary statistics from backtest."""

    # Overall stats
    total_markets_fetched: int
    total_binary_markets: int
    total_snapshots_attempted: int
    total_trades: int

    # Performance
    win_rate: float  # Percentage of winning trades
    avg_return: float  # Average return per trade
    median_return: float
    total_pnl: float

    # Distribution
    percentile_5: float
    percentile_25: float
    percentile_75: float
    percentile_95: float

    # Breakdown
    yes_longshots: int  # Trades where YES was longshot (we bought NO)
    no_longshots: int  # Trades where NO was longshot (we bought YES)

    # Time
    backtest_start_date: datetime
    backtest_end_date: datetime
    run_time_seconds: float
