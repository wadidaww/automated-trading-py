"""Pipeline stages, the stage-factory interface and the pipeline runner."""

from trader.pipeline.base import IStage
from trader.pipeline.factory import IPipelineFactory

__all__ = ["IPipelineFactory", "IStage"]
