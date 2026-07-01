"""Abstract Factory for pipeline stage creation."""

from __future__ import annotations

from abc import ABC, abstractmethod

from futu_trader.api.client import FutuClient
from futu_trader.execution.order_manager import OrderManager
from futu_trader.model.mean_reversion import MeanReversionModel
from futu_trader.pipeline.audit_stage import AuditStage
from futu_trader.pipeline.data_stage import DataStage
from futu_trader.pipeline.execution_stage import ExecutionStage
from futu_trader.pipeline.risk_stage import RiskStage
from futu_trader.pipeline.signal_stage import SignalStage
from futu_trader.risk.risk_engine import RiskEngine


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
    """Concrete factory that builds the standard live-trading pipeline stages."""

    def create_data_stage(self) -> DataStage:
        return DataStage()

    def create_signal_stage(self) -> SignalStage:
        return SignalStage(MeanReversionModel(), confidence_threshold=0.0)

    def create_risk_stage(self) -> RiskStage:
        return RiskStage(
            RiskEngine(
                max_symbol_notional_minor=10_000_000,
                max_portfolio_notional_minor=50_000_000,
                max_daily_loss_minor=1_000_000,
                max_open_orders=50,
                concentration_limit_pct=0.5,
            )
        )

    def create_execution_stage(self) -> ExecutionStage:
        return ExecutionStage(OrderManager(FutuClient()))

    def create_audit_stage(self) -> AuditStage:
        return AuditStage()
