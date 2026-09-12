from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

torch = pytest.importorskip("torch")

from trader.model.base import Signal  # noqa: E402
from trader.model.transformer_model import TransformerPriceModel  # noqa: E402
from helpers import model_features  # noqa: E402


def test_transformer_predict_returns_signal_probabilities() -> None:
    model = TransformerPriceModel(
        input_size=3,
        window_size=4,
        hidden_size=12,
        num_heads=2,
        layers=1,
        dropout=0.0,
    )

    prediction = model.predict(model_features())

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
    )
    # Intentionally mix enum, string, lower-case string, and integer label formats.
    labels = pd.Series([Signal.SELL, "HOLD", Signal.BUY, "buy", 0, -1, "HOLD", 1])
    assert model.net is not None
    initial_head = model.net.head.weight.detach().clone()

    model.fit(model_features(), labels)
    assert model.net is not None
    assert not torch.allclose(initial_head, model.net.head.weight)

    path = tmp_path / "transformer.pt"
    model.save(str(path))

    loaded = TransformerPriceModel.load(str(path))
    prediction = loaded.predict(model_features())

    assert prediction.signal in Signal
    assert prediction.metadata["window_size"] == 3
    assert loaded.feature_columns == ["close", "volume", "return_1d"]


@pytest.mark.parametrize("label", [2, "INVALID", None])
def test_transformer_rejects_invalid_labels(label: object) -> None:
    with pytest.raises(ValueError, match="target signal|string|target values"):
        TransformerPriceModel._target_to_index(label)
