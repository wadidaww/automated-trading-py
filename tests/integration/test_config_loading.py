from __future__ import annotations

from trader.utils.config import load_config


def test_config_loading() -> None:
    cfg = load_config("config/config.dev.yaml")
    assert cfg.opend.port == 11111
