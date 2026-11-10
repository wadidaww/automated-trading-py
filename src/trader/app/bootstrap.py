"""Composition root: build the pipeline stages from config and a broker client.

``DefaultPipelineFactory`` is the one place that turns ``AppConfig`` into concrete objects:
the broker adapter (when none is given), the signal model (via ``trader.model.registry``), the
pre-trade risk service with its kill switch and throttle, and the order manager.
"""

from __future__ import annotations

import asyncio
from typing import TypedDict

from trader.adapters.factory import create_client
from trader.adapters.futu.client import FutuClient
from trader.ports.broker import BrokerClient
from trader.execution.order_manager import OrderManager
from trader.model.base import ISignalModel
from trader.model.registry import DEFAULT_MODEL_TYPE, build_model
from trader.pipeline.audit_stage import AuditStage
from trader.pipeline.data_stage import DataStage
from trader.pipeline.execution_stage import ExecutionStage
from trader.pipeline.factory import IPipelineFactory
from trader.pipeline.risk_stage import PreTradeLimits, RiskStage
from trader.risk.pre_trade import VolTargetSizing
from trader.pipeline.signal_stage import SignalStage
from trader.risk.kelly_criterion import KellyCriterion
from trader.risk.kill_switch import KillSwitch
from trader.risk.risk_engine import RiskEngine
from trader.risk.throttle import OrderRateThrottle
from trader.config import TREND_MODEL_TYPE, AppConfig, RiskSettings
from trader.data.features import LIVE_FEATURES, TREND_FEATURES, TrendSpec
from trader.utils.logger import get_logger
from trader.utils.maths import to_minor_units

logger = get_logger("factory")

DEFAULT_WINDOW_SIZE = 100
DEFAULT_CONFIDENCE_THRESHOLD = 0.65
DEFAULT_STRATEGY = "default"


class RiskEngineKwargs(TypedDict):
    """Risk engine limit arguments shared by config and default construction."""

    max_symbol_notional_minor: int
    max_portfolio_notional_minor: int
    max_daily_loss_minor: int
    max_open_orders: int
    concentration_limit_pct: float


class DefaultPipelineFactory(IPipelineFactory):
    """Concrete factory that builds pipeline stages.

    When a config and/or client are provided, stages are parameterized
    from the configuration. Without them, safe defaults are used for
    backward compatibility with tests and development.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        client: BrokerClient | None = None,
    ) -> None:
        self._config = config
        self._client = client or (create_client(config, "paper") if config else FutuClient())
        self._risk_settings = config.risk if config is not None else RiskSettings()
        self.kill_switch = KillSwitch(self._risk_settings.kill_switch_file)
        self.kill_switch.on_trip(self._cancel_all_on_trip)
        self._background: set[asyncio.Task[None]] = set()

    def create_data_stage(self) -> DataStage:
        """Create DataStage with configured window size."""
        window_size = (
            self._config.pipeline.data_window_size if self._config else DEFAULT_WINDOW_SIZE
        )
        trend = self._trend_spec()
        logger.info("creating_data_stage", window_size=window_size, trend=trend is not None)
        return DataStage(window_size=window_size, trend=trend)

    def _trend_spec(self) -> TrendSpec | None:
        """Trend feature lookbacks, only when the configured model is the trend model."""
        if self._config is None or self._config.model.type != TREND_MODEL_TYPE:
            return None
        trend = self._config.model.trend
        return TrendSpec(
            lookbacks=tuple(trend.lookbacks),
            donchian_entry=trend.donchian_entry,
            donchian_exit=trend.donchian_exit,
            vol_window=trend.vol_window,
            bars_per_year=trend.bars_per_year,
        )

    def create_signal_stage(self) -> SignalStage:
        """Create SignalStage with configured model and threshold.

        Supports model types:
        - "mean_reversion": Enhanced mean reversion with momentum confirmation
        - "ensemble": Weighted ensemble of mean reversion models
        - "trend": Rule-based trend following; states a target exposure sized by
          ``risk.vol_target`` in the risk stage
        - "gradient_boosting", "tabular" and every ``TabularModel`` estimator: fitted artifacts
          loaded from ``model.path``; startup fails if the artifact is missing
        """
        model_cfg = self._config.model if self._config else None
        threshold = model_cfg.confidence_threshold if model_cfg else DEFAULT_CONFIDENCE_THRESHOLD
        model_type = model_cfg.type if model_cfg else DEFAULT_MODEL_TYPE

        model = self._create_model(model_type)
        logger.info(
            "creating_signal_stage",
            model_type=model_type,
            confidence_threshold=threshold,
        )
        columns = (
            (*LIVE_FEATURES, *TREND_FEATURES) if model_type == TREND_MODEL_TYPE else LIVE_FEATURES
        )
        return SignalStage(model, confidence_threshold=threshold, feature_columns=columns)

    def _create_model(self, model_type: str) -> ISignalModel:
        """Build the model for ``model_type`` from ``trader.model.registry``."""
        return build_model(model_type, self._config.model if self._config else None)

    def create_risk_stage(self) -> RiskStage:
        """Create RiskStage with configured limits, the shared kill switch and a throttle."""
        engine = RiskEngine(**self._risk_engine_kwargs())
        risk = self._risk_settings
        limits = PreTradeLimits(
            max_order_qty=risk.max_order_qty,
            max_order_notional_minor=to_minor_units(risk.max_order_notional),
            price_band_pct=risk.price_band_pct,
            allow_short=risk.allow_short,
        )
        throttle = OrderRateThrottle(risk.max_orders_per_second, risk.max_orders_per_30s)
        strategy = self._config.trading.strategy_name if self._config else DEFAULT_STRATEGY
        sizing = risk.vol_target
        vol_target = (
            VolTargetSizing(
                target_vol_annual=sizing.target_vol_annual,
                symbol_weight=sizing.symbol_weight,
                rebalance_band=sizing.rebalance_band,
            )
            if sizing.enabled
            else None
        )
        logger.info("creating_risk_stage", strategy=strategy, vol_target=sizing.enabled)
        return RiskStage(
            engine,
            client=self._client,
            limits=limits,
            kill_switch=self.kill_switch,
            throttle=throttle,
            kelly=KellyCriterion(),
            strategy=strategy,
            vol_target=vol_target,
        )

    def _cancel_all_on_trip(self, reason: str) -> None:
        """Kill-switch callback: cancel every working order in the background."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("kill_switch_cancel_skipped_no_loop", reason=reason)
            return
        task = loop.create_task(self._cancel_all(reason))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _cancel_all(self, reason: str) -> None:
        """Cancel all working orders, logging (not raising) on failure."""
        try:
            await self._client.cancel_all_orders()
            logger.warning("kill_switch_cancelled_all_orders", reason=reason)
        except Exception:
            logger.exception("kill_switch_cancel_all_failed", reason=reason)

    def _risk_engine_kwargs(self) -> RiskEngineKwargs:
        """Resolve risk limits from config, falling back to safe defaults."""
        if self._config is None:
            return RiskEngineKwargs(
                max_symbol_notional_minor=10_000_000,
                max_portfolio_notional_minor=50_000_000,
                max_daily_loss_minor=1_000_000,
                max_open_orders=50,
                concentration_limit_pct=0.5,
            )
        trading = self._config.trading
        return RiskEngineKwargs(
            max_symbol_notional_minor=to_minor_units(trading.max_position_notional),
            max_portfolio_notional_minor=to_minor_units(trading.max_portfolio_notional),
            max_daily_loss_minor=to_minor_units(trading.max_daily_loss),
            max_open_orders=trading.max_open_orders,
            concentration_limit_pct=trading.concentration_limit_pct,
        )

    def create_execution_stage(self) -> ExecutionStage:
        """Create ExecutionStage with shared client."""
        logger.info("creating_execution_stage")
        return ExecutionStage(OrderManager(self._client, kill_switch=self.kill_switch))

    def create_audit_stage(self) -> AuditStage:
        """Create AuditStage."""
        return AuditStage()
