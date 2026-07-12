from __future__ import annotations

import pandas as pd

from trader.model.mean_reversion import MeanReversionModel


def test_mean_reversion_round_trip(tmp_path) -> None:
    path = tmp_path / "model.json"
    model = MeanReversionModel()
    model.save(str(path))
    loaded = MeanReversionModel.load(str(path))
    pred = loaded.predict(pd.DataFrame({"z_score": [-3.0]}))
    assert pred.metadata["z_score"] == -3.0
