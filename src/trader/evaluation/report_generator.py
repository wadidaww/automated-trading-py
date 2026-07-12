"""Backtest report generation."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px


class ReportGenerator:
    """Generate JSON/HTML/markdown backtest reports."""

    def to_json(self, payload: dict[str, float], path: str) -> None:
        """Write JSON report."""
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def to_html(self, equity_curve: pd.Series, path: str) -> None:
        """Write HTML report with equity curve."""
        fig = px.line(equity_curve)
        fig.write_html(path)

    def to_markdown(self, payload: dict[str, float]) -> str:
        """Generate markdown summary table."""
        rows = "\n".join([f"| {k} | {v:.4f} |" for k, v in payload.items()])
        return f"| metric | value |\n|---|---|\n{rows}\n"
