"""Model registry with optional hot-reload lock."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from futu_trader.model.base import ISignalModel
from futu_trader.model.mean_reversion import MeanReversionModel


class ModelRegistry:
    """File-backed model registry."""

    def __init__(self, model_dir: str = "data/models") -> None:
        self.model_dir = Path(model_dir)
        self._lock = RLock()

    def load(self, name: str, version: str = "latest") -> ISignalModel:
        """Load model by name/version from model directory."""
        with self._lock:
            path = self.model_dir / f"{name}_{version}.json"
            if path.exists():
                return MeanReversionModel.load(str(path))
            return MeanReversionModel()
