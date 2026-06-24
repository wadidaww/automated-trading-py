from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

torch = pytest.importorskip("torch")

from futu_trader.model.base import Signal  # noqa: E402
from futu_trader.model.transformer_model import TransformerPriceModel  # noqa: E402


def _features(rows: int = 8) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [100.0 + index for index in range(rows)],
            "volume": [1_000.0 + index * 10.0 for index in range(rows)],
            "return_1d": [index / 100.0 for index in range(rows)],
        }
    )


def test_transformer_predict_returns_signal_probabilities() -> None:
    model = TransformerPriceModel(
        input_size=3,
        window_size=4,
        hidden_size=12,
        num_heads=2,
        layers=1,
        dropout=0.0,
    )

    prediction = model.predict(_features())

    assert prediction.signal in Signal
    assert 0.0 <= prediction.confidence <= 1.0
    assert set(prediction.metadata["probabilities"]) == {"SELL", "HOLD", "BUY"}
    assert prediction.metadata["model"] == "transformer_price"


def test_transformer_fit_and_round_trip(tmp_path: Path) -> None:
    model = TransformerPriceModel(
        input_size=3,
        window_size=3,
        hidden_size=8,
        num_heads=2,
        layers=1,
        dropout=0.0,
        epochs=1,
        batch_size=2,
        seed=7,
    )
    labels = pd.Series([-1, 0, 1, 1, 0, -1, 0, 1])
    assert model.net is not None
    initial_head = model.net.head.weight.detach().clone()

    model.fit(_features(), labels)
    assert model.net is not None
    assert not torch.allclose(initial_head, model.net.head.weight)

    path = tmp_path / "transformer.pt"
    model.save(str(path))

    loaded = TransformerPriceModel.load(str(path))
    prediction = loaded.predict(_features())

    assert prediction.signal in Signal
    assert prediction.metadata["window_size"] == 3
    assert loaded.feature_columns == ["close", "volume", "return_1d"]
