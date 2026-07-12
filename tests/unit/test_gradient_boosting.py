from __future__ import annotations

import pandas as pd

from trader.model.gradient_boosting import GradientBoostingModel


def test_gradient_boosting_fit_predict() -> None:
    model = GradientBoostingModel(random_state=1)
    X = pd.DataFrame({"x1": [0, 1, 0, 1], "x2": [1, 0, 1, 0]})
    y = pd.Series([0, 1, 0, 1])
    model.fit(X, y)
    pred = model.predict(X.tail(1))
    assert 0.0 <= pred.confidence <= 1.0
