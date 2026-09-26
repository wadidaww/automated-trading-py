"""Abstract Factory for pipeline stage creation."""

from __future__ import annotations

from abc import ABC, abstractmethod

from trader.api.client import FutuClient
from trader.execution.order_manager import OrderManager
from trader.model.base import ISignalModel
from trader.model.ensemble import EnsembleSignalModel
from trader.model.mean_reversion import MeanReversionModel
from trader.pipeline.audit_stage import AuditStage
from trader.pipeline.data_stage import DataStage
from trader.pipeline.execution_stage import ExecutionStage
from trader.pipeline.risk_stage import RiskStage
from trader.pipeline.signal_stage import SignalStage
from trader.risk.kelly_criterion import KellyCriterion
from trader.risk.risk_engine import RiskEngine
from trader.utils.config import AppConfig
from trader.utils.logger import get_logger
from trader.utils.maths import to_minor_units

logger = get_logger("factory")


class IPipelineFactory(ABC):
    """Abstract factory that creates a cohesive family of pipeline stages."""

    @abstractmethod
    def create_data_stage(self) -> DataStage:
        """Create and return a DataStage instance."""

    @abstractmethod
    def create_signal_stage(self) -> SignalStage:
        """Create and return a SignalStage instance."""

    @abstractmethod
    def create_risk_stage(self) -> RiskStage:
        """Create and return a RiskStage instance."""

    @abstractmethod
    def create_execution_stage(self) -> ExecutionStage:
        """Create and return an ExecutionStage instance."""

    @abstractmethod
    def create_audit_stage(self) -> AuditStage:
        """Create and return an AuditStage instance."""


class DefaultPipelineFactory(IPipelineFactory):
    """Concrete factory that builds pipeline stages.

    When a config and/or client are provided, stages are parameterized
    from the configuration. Without them, safe defaults are used for
    backward compatibility with tests and development.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        client: FutuClient | None = None,
    ) -> None:
        self._config = config
        self._client = client or FutuClient()

    def create_data_stage(self) -> DataStage:
        """Create DataStage with configured window size."""
        window_size = 100
        if self._config is not None:
            window_size = self._config.pipeline.data_window_size
        logger.info("creating_data_stage", window_size=window_size)
        return DataStage(window_size=window_size)

    def create_signal_stage(self) -> SignalStage:
        """Create SignalStage with configured model and threshold.

        Supports model types:
        - "mean_reversion": Enhanced mean reversion with momentum confirmation
        - "ensemble": Weighted ensemble of mean reversion models
        """
        threshold = 0.65
        model_type = "mean_reversion"
        if self._config is not None:
            threshold = self._config.model.confidence_threshold
            model_type = self._config.model.type

        model = self._create_model(model_type)
        logger.info(
            "creating_signal_stage",
            model_type=model_type,
            confidence_threshold=threshold,
        )
        return SignalStage(model, confidence_threshold=threshold)

    def _create_model(self, model_type: str) -> ISignalModel:
        """Create model instance based on type string."""
        if model_type == "ensemble":
            return self._create_ensemble_model()
        if model_type == "gradient_boosting":
            try:
                from trader.model.gradient_boosting import GradientBoostingModel

                return GradientBoostingModel()
            except ImportError:
                logger.warning("gradient_boosting_not_available, falling back to mean_reversion")
        return MeanReversionModel()

    def _create_ensemble_model(self) -> EnsembleSignalModel:
        """Create ensemble model with multiple mean reversion variants."""
        ensemble = EnsembleSignalModel()
        ensemble.add_model(
            MeanReversionModel(buy_threshold=-2.0, sell_threshold=2.0),
            weight=0.4,
            name="standard",
        )
        ensemble.add_model(
            MeanReversionModel(buy_threshold=-1.5, sell_threshold=1.5),
            weight=0.3,
            name="aggressive",
        )
        ensemble.add_model(
            MeanReversionModel(buy_threshold=-2.5, sell_threshold=2.5, volatility_adjust=True),
            weight=0.3,
            name="conservative",
        )
        return ensemble

    def create_risk_stage(self) -> RiskStage:
        """Create RiskStage with configured limits."""
        if self._config is not None:
            trading = self._config.trading
            engine = RiskEngine(
                max_symbol_notional_minor=to_minor_units(trading.max_position_notional_hkd),
                max_portfolio_notional_minor=to_minor_units(trading.max_portfolio_notional_hkd),
                max_daily_loss_minor=to_minor_units(trading.max_daily_loss_hkd),
                max_open_orders=trading.max_open_orders,
                concentration_limit_pct=trading.concentration_limit_pct,
            )
        else:
            engine = RiskEngine(
                max_symbol_notional_minor=10_000_000,
                max_portfolio_notional_minor=50_000_000,
                max_daily_loss_minor=1_000_000,
                max_open_orders=50,
                concentration_limit_pct=0.5,
            )
        logger.info("creating_risk_stage")
        return RiskStage(engine, client=self._client, kelly=KellyCriterion())

    def create_execution_stage(self) -> ExecutionStage:
        """Create ExecutionStage with shared client."""
        logger.info("creating_execution_stage")
        return ExecutionStage(OrderManager(self._client))

    def create_audit_stage(self) -> AuditStage:
        """Create AuditStage."""
        return AuditStage()
