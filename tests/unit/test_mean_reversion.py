from __future__ import annotations

import pandas as pd

from trader.model.base import Signal
from trader.model.mean_reversion import MeanReversionModel


def test_mean_reversion_buy_signal() -> None:
    model = MeanReversionModel()
    pred = model.predict(pd.DataFrame({"z_score": [-3.0]}))
    assert pred.signal is Signal.BUY
