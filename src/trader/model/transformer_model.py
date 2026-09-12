"""Transformer-based stock price signal model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Self, cast

import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from trader.model.base import ISignalModel, Prediction, Signal


SIGNALS: tuple[Signal, Signal, Signal] = (Signal.SELL, Signal.HOLD, Signal.BUY)
SIGNAL_TO_INDEX: dict[Signal, int] = {signal: index for index, signal in enumerate(SIGNALS)}
INTEGER_TO_SIGNAL: dict[int, Signal] = {-1: Signal.SELL, 0: Signal.HOLD, 1: Signal.BUY}
POSITION_EMBEDDING_INIT_STD = 0.02
FEEDFORWARD_EXPANSION_FACTOR = 4


@dataclass(slots=True)
class TransformerConfig:
    """Hyperparameters for the transformer price model."""

    input_size: int | None = None
    window_size: int = 20
    hidden_size: int = 64
    num_heads: int = 4
    layers: int = 2
    dropout: float = 0.1
    learning_rate: float = 1e-3
    epochs: int = 10
    batch_size: int = 32
    seed: int = 42


class PositionalEncoding(nn.Module):
    """Learned positional encoding for fixed-length market windows."""

    def __init__(self, window_size: int, hidden_size: int) -> None:
        super().__init__()
        self.embedding = nn.Parameter(
            torch.randn(1, window_size, hidden_size) * POSITION_EMBEDDING_INIT_STD
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Add position embeddings to projected feature windows."""
        return x + self.embedding[:, : x.shape[1], :]


class TransformerPriceNet(nn.Module):
    """Transformer encoder classifier for tabular price-feature windows."""

    def __init__(self, config: TransformerConfig) -> None:
        super().__init__()
        if config.input_size is None:
            msg = "input_size must be known before building TransformerPriceNet"
            raise ValueError(msg)
        if config.hidden_size % config.num_heads != 0:
            msg = "hidden_size must be divisible by num_heads"
            raise ValueError(msg)

        self.projection = nn.Linear(config.input_size, config.hidden_size)
        self.position = PositionalEncoding(config.window_size, config.hidden_size)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=config.hidden_size,
            nhead=config.num_heads,
            dim_feedforward=config.hidden_size * FEEDFORWARD_EXPANSION_FACTOR,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=config.layers)
        self.norm = nn.LayerNorm(config.hidden_size)
        self.head = nn.Linear(config.hidden_size, len(SIGNALS))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Return class logits for input shaped as (batch, sequence, features)."""
        encoded = self.projection(x)
        encoded = self.position(encoded)
        encoded = self.encoder(encoded)
        pooled = self.norm(encoded.mean(dim=1))
        return cast("torch.Tensor", self.head(pooled))


class TransformerPriceModel(ISignalModel):
    """Transformer encoder model for stock price movement prediction."""

    def __init__(
        self,
        input_size: int | None = None,
        window_size: int = 20,
        hidden_size: int = 64,
        num_heads: int = 4,
        layers: int = 2,
        dropout: float = 0.1,
        learning_rate: float = 1e-3,
        epochs: int = 10,
        batch_size: int = 32,
        seed: int = 42,
        feature_columns: list[str] | None = None,
    ) -> None:
        self.config = TransformerConfig(
            input_size=input_size,
            window_size=window_size,
            hidden_size=hidden_size,
            num_heads=num_heads,
            layers=layers,
            dropout=dropout,
            learning_rate=learning_rate,
            epochs=epochs,
            batch_size=batch_size,
            seed=seed,
        )
        self.feature_columns = feature_columns
        self.net: TransformerPriceNet | None = None
        if input_size is not None:
            self._build_net()

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """Train the transformer classifier on rolling feature windows."""
        features = self._prepare_features(X, fit=True)
        target_indexes = y.map(self._target_to_index).to_numpy(dtype="int64")
        targets = torch.tensor(target_indexes, dtype=torch.long)
        windows, labels = self._build_training_windows(features, targets)

        self._build_net()
        if self.net is None:
            msg = "model network was not initialized"
            raise RuntimeError(msg)

        torch.manual_seed(self.config.seed)
        dataset = TensorDataset(windows, labels)
        loader = DataLoader(dataset, batch_size=self.config.batch_size, shuffle=True)
        optimizer = torch.optim.AdamW(self.net.parameters(), lr=self.config.learning_rate)
        loss_fn = nn.CrossEntropyLoss()

        self.net.train()
        for _ in range(self.config.epochs):
            for batch_x, batch_y in loader:
                optimizer.zero_grad(set_to_none=True)
                logits = self.net(batch_x)
                loss = loss_fn(logits, batch_y)
                loss.backward()
                optimizer.step()

    def predict(self, features: pd.DataFrame) -> Prediction:
        """Predict the next price-movement signal from the latest feature window."""
        feature_tensor = self._prepare_features(features, fit=False)
        window = self._latest_window(feature_tensor)

        if self.net is None:
            self._build_net()
        if self.net is None:
            msg = "model must be fitted or initialized with input_size before prediction"
            raise ValueError(msg)

        self.net.eval()
        with torch.no_grad():
            logits = self.net(window.unsqueeze(0))
            probabilities = torch.softmax(logits, dim=-1).squeeze(0)

        index = int(probabilities.argmax().item())
        confidence = float(probabilities[index].item())
        return Prediction(
            signal=SIGNALS[index],
            confidence=confidence,
            metadata={
                "probabilities": {
                    signal.value: float(probabilities[signal_index].item())
                    for signal_index, signal in enumerate(SIGNALS)
                },
                "window_size": self.config.window_size,
                "model": "transformer_price",
            },
        )

    def save(self, path: str) -> None:
        """Serialize model configuration, feature schema, and learned weights."""
        if self.net is None:
            msg = "cannot save an uninitialized transformer model"
            raise ValueError(msg)
        torch.save(
            {
                "config": asdict(self.config),
                "feature_columns": self.feature_columns,
                "state_dict": self.net.state_dict(),
            },
            path,
        )

    @classmethod
    def load(cls, path: str) -> Self:
        """Load a serialized transformer model artifact."""
        payload: dict[str, Any] = torch.load(path, map_location="cpu", weights_only=True)
        config = payload["config"]
        feature_columns_payload = payload.get("feature_columns")
        feature_columns = (
            feature_columns_payload
            if isinstance(feature_columns_payload, list)
            and all(isinstance(column, str) for column in feature_columns_payload)
            else None
        )
        instance = cls(**config, feature_columns=feature_columns)
        if instance.net is None:
            instance._build_net()
        if instance.net is None:
            msg = "serialized transformer model is missing network configuration"
            raise ValueError(msg)
        instance.net.load_state_dict(payload["state_dict"])
        instance.net.eval()
        return instance

    def _build_net(self) -> None:
        torch.manual_seed(self.config.seed)
        self.net = TransformerPriceNet(self.config)

    def _prepare_features(self, features: pd.DataFrame, *, fit: bool) -> torch.Tensor:
        if features.empty:
            msg = "features must contain at least one row"
            raise ValueError(msg)

        if fit or self.feature_columns is None:
            self.feature_columns = list(features.columns)
        if not self.feature_columns:
            msg = "features must contain at least one column"
            raise ValueError(msg)

        missing = [column for column in self.feature_columns if column not in features.columns]
        if missing:
            msg = f"features are missing required columns: {missing}"
            raise ValueError(msg)

        frame = features.loc[:, self.feature_columns].astype("float32")
        if self.config.input_size is None:
            self.config.input_size = len(self.feature_columns)
        return torch.tensor(frame.to_numpy(), dtype=torch.float32)

    def _latest_window(self, features: torch.Tensor) -> torch.Tensor:
        """Return a fixed-size window, prepending zero rows when history is short."""
        if len(features) >= self.config.window_size:
            return features[-self.config.window_size :]

        zero_padding = features.new_zeros(
            (self.config.window_size - len(features), features.shape[1])
        )
        return torch.cat((zero_padding, features), dim=0)

    def _build_training_windows(
        self, features: torch.Tensor, targets: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if len(features) != len(targets):
            msg = "features and targets must have the same number of rows"
            raise ValueError(msg)

        zero_padding = features.new_zeros((self.config.window_size - 1, features.shape[1]))
        padded_features = torch.cat((zero_padding, features), dim=0)
        windows = padded_features.unfold(0, self.config.window_size, 1).transpose(1, 2).contiguous()
        return windows, targets

    @staticmethod
    def _target_to_index(value: object) -> int:
        if isinstance(value, Signal):
            return SIGNAL_TO_INDEX[value]
        if isinstance(value, str):
            normalized_value = value.upper()
            try:
                return SIGNAL_TO_INDEX[Signal(normalized_value)]
            except ValueError as exc:
                valid_values = ", ".join(signal.value for signal in SIGNALS)
                msg = f"invalid target signal string: {value!r}. Valid values are: {valid_values}"
                raise ValueError(msg) from exc
        if isinstance(value, int) and value in INTEGER_TO_SIGNAL:
            return SIGNAL_TO_INDEX[INTEGER_TO_SIGNAL[value]]

        msg = (
            "target values must be Signal, signal strings, or -1/0/1 integers, "
            f"got {value!r} of type {type(value).__name__}"
        )
        raise ValueError(msg)
