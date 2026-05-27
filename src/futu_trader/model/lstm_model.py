"""Torch LSTM model wrapper."""

from __future__ import annotations

import torch
from torch import nn


class LSTMNet(nn.Module):  # type: ignore[misc]
    """Simple LSTM classifier."""

    def __init__(
        self, input_size: int, hidden_size: int = 32, layers: int = 1, dropout: float = 0.0
    ) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=layers,
            batch_first=True,
            dropout=dropout if layers > 1 else 0.0,
        )
        self.head = nn.Linear(hidden_size, 3)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass for (batch, seq, features)."""
        output, _ = self.lstm(x)
        logits = self.head(output[:, -1, :])
        return torch.softmax(logits, dim=-1)


class LSTMModel:
    """Higher-level model facade for training/export routines."""

    def __init__(
        self, input_size: int, hidden_size: int = 32, layers: int = 1, dropout: float = 0.0
    ) -> None:
        self.net = LSTMNet(
            input_size=input_size, hidden_size=hidden_size, layers=layers, dropout=dropout
        )

    def save(self, path: str) -> None:
        """Save model state dict."""
        torch.save(self.net.state_dict(), path)

    def load(self, path: str) -> None:
        """Load model state dict."""
        self.net.load_state_dict(torch.load(path, map_location="cpu"))
