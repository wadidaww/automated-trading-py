"""Risk stage."""

from __future__ import annotations

from dataclasses import dataclass

from trader.api.client import FutuClient, find_position_by_symbol
from trader.misc.types.futu import TradeSide
from trader.pipeline.base import IStage
from trader.pipeline.signal_stage import TradeSignal
from trader.risk.kelly_criterion import KellyCriterion
from trader.risk.risk_engine import RiskEngine, RiskInput
from trader.utils.logger import get_logger
from trader.utils.maths import to_minor_units

logger = get_logger("risk_stage")

DEFAULT_PORTFOLIO_VALUE_MINOR = 1_000_000
MAX_POSITION_QTY = 1000

CONFIDENCE_SIZE_TIERS: tuple[tuple[float, float], ...] = (
    (0.85, 1.0),
    (0.70, 0.6),
)
DEFAULT_SIZE_MULTIPLIER = 0.3


@dataclass(slots=True)
class ApprovedOrder:
    """Order approved by risk."""

    symbol: str
    qty: int
    side: TradeSide
    price: float


@dataclass(slots=True)
class PortfolioState:
    """Portfolio snapshot consumed by risk evaluation."""

    portfolio_value_minor: int
    symbol_notional_minor: int
    current_positions_notional_minor: int
    daily_pnl_minor: int
    open_orders: int


class RiskStage(IStage[TradeSignal, ApprovedOrder | None]):
    """Risk gate stage.

    Evaluates signals against portfolio limits and position sizing rules.
    When a FutuClient is provided, queries live portfolio state for accurate
    risk checks. Without a client (test mode), uses safe default values.
    """

    def __init__(
        self,
        engine: RiskEngine,
        client: FutuClient | None = None,
        kelly: KellyCriterion | None = None,
    ) -> None:
        self.engine = engine
        self._client = client
        self._kelly = kelly or KellyCriterion()
        self.signals_dropped = 0

    async def process(self, item: TradeSignal) -> ApprovedOrder | None:
        """Evaluate signal against limits.

        Args:
            item: Trade signal from signal stage.

        Returns:
            ApprovedOrder if risk allows, None if rejected.
        """
        try:
            state = await self._get_portfolio_state(item.symbol)
            return await self._evaluate_signal(item, state)
        except Exception:
            logger.warning("risk_evaluation_failed", symbol=item.symbol, exc_info=True)
            self.signals_dropped += 1
            return None

    async def _evaluate_signal(
        self, item: TradeSignal, state: PortfolioState
    ) -> ApprovedOrder | None:
        """Size the order and run the hard gates for one signal.

        Args:
            item: Trade signal to evaluate.
            state: Current portfolio snapshot.

        Returns:
            ApprovedOrder when risk allows, None when a gate rejects.
        """
        qty = await self._compute_quantity(item, state.portfolio_value_minor)
        decision = self.engine.evaluate(
            RiskInput(
                symbol=item.symbol,
                quantity=qty,
                price_minor=to_minor_units(item.price),
                current_symbol_notional_minor=state.symbol_notional_minor,
                current_portfolio_notional_minor=state.current_positions_notional_minor,
                daily_pnl_minor=state.daily_pnl_minor,
                open_orders=state.open_orders,
                portfolio_value_minor=state.portfolio_value_minor,
            )
        )
        if not decision.approved:
            self.signals_dropped += 1
            logger.info(
                "signal_rejected",
                symbol=item.symbol,
                reason=decision.reason,
                signal=item.signal.value,
            )
            return None

        side: TradeSide = "BUY" if item.signal.value == "BUY" else "SELL"
        return ApprovedOrder(symbol=item.symbol, qty=qty, side=side, price=item.price)

    async def _get_portfolio_state(self, symbol: str) -> PortfolioState:
        """Fetch live portfolio state from Futu or return defaults.

        Returns:
            PortfolioState with portfolio totals, or safe defaults when no
            client is configured or the fetch fails.
        """
        if self._client is None:
            return PortfolioState(DEFAULT_PORTFOLIO_VALUE_MINOR, 0, 0, 0, 0)

        try:
            portfolio = await self._client.get_portfolio()
            positions = await self._client.get_positions()
            orders = await self._client.list_orders()

            symbol_pos = find_position_by_symbol(positions, symbol)
            return PortfolioState(
                portfolio_value_minor=to_minor_units(portfolio.total_assets),
                symbol_notional_minor=(
                    to_minor_units(symbol_pos.market_value) if symbol_pos else 0
                ),
                current_positions_notional_minor=sum(
                    to_minor_units(pos.market_value) for pos in positions
                ),
                daily_pnl_minor=to_minor_units(portfolio.unrealized_pnl or 0.0),
                open_orders=len([o for o in orders if o.status not in ("FILLED", "CANCELLED")]),
            )
        except Exception:
            logger.warning("portfolio_fetch_failed", exc_info=True)
            return PortfolioState(DEFAULT_PORTFOLIO_VALUE_MINOR, 0, 0, 0, 0)

    async def _compute_quantity(self, item: TradeSignal, portfolio_value_minor: int) -> int:
        """Compute position size using Kelly criterion with confidence scaling.

        Scales position size based on signal confidence:
        - High confidence (>=0.85): Full Kelly allocation
        - Medium confidence (0.70-0.85): 60% of Kelly allocation
        - Low confidence (<0.70): 30% of Kelly allocation

        Falls back to qty=1 when Kelly returns 0 or when sizing fails.

        Args:
            item: Trade signal with confidence and price.
            portfolio_value_minor: Current portfolio value in minor units.

        Returns:
            Number of shares to trade.
        """
        try:
            if item.confidence <= 0:
                return 1

            kelly_fraction = self._kelly_fraction(item.confidence)
            if kelly_fraction <= 0:
                return 1

            price_minor = to_minor_units(item.price)
            if price_minor <= 0:
                return 1

            size_multiplier = self._size_multiplier(item.confidence)
            notional_budget = int(portfolio_value_minor * kelly_fraction * size_multiplier)
            return max(1, min(notional_budget // price_minor, MAX_POSITION_QTY))
        except Exception:
            return 1

    def _kelly_fraction(self, confidence: float) -> float:
        """Derive the Kelly fraction for a signal confidence value."""
        win_rate = min(confidence, 0.95)
        win_loss_ratio = 1.0 / max(1.0 - win_rate, 0.05)
        return self._kelly.calculate(win_rate, win_loss_ratio)

    @staticmethod
    def _size_multiplier(confidence: float) -> float:
        """Look up the confidence tier multiplier from CONFIDENCE_SIZE_TIERS."""
        return next(
            (mult for floor, mult in CONFIDENCE_SIZE_TIERS if confidence >= floor),
            DEFAULT_SIZE_MULTIPLIER,
        )
