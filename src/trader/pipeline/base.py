"""Abstract base for pipeline stages."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class IStage(ABC, Generic[InputT, OutputT]):
    """Common interface for all pipeline stages."""

    @abstractmethod
    async def process(self, item: InputT) -> OutputT:
        """Process a single pipeline item and return the result."""
