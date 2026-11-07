"""Abstract Factory for pipeline stage creation.

The concrete factory that wires config, broker client, models and risk into the stages is
``trader.app.bootstrap.DefaultPipelineFactory``: the pipeline only knows this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from trader.pipeline.audit_stage import AuditStage
from trader.pipeline.data_stage import DataStage
from trader.pipeline.execution_stage import ExecutionStage
from trader.pipeline.risk_stage import RiskStage
from trader.pipeline.signal_stage import SignalStage


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
