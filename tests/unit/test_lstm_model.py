from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from futu_trader.model.lstm_model import LSTMModel  # noqa: E402


def test_lstm_forward_shape() -> None:
    model = LSTMModel(input_size=4)
    x = torch.rand(2, 5, 4)
    y = model.net(x)
    assert tuple(y.shape) == (2, 3)
