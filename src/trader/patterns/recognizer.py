"""A 1-D convolutional network that recognizes chart patterns in a window of OHLC bars.

The model sees a window resampled to a fixed number of steps, with open/high/low/close scaled to
the window's own range, so it is invariant to price level and pattern duration. It is trained on
``synthetic.pattern_dataset`` and can be fine-tuned on real windows labelled by the rule
detector in ``chart`` (weak labels): the synthetic examples teach the shapes, the real ones the
texture of real prices.

``scan`` slides windows of several lengths over a history and keeps confident, non-overlapping
detections. Recognising a shape is not the same as predicting returns: check
``stats.pattern_outcomes`` before trading on any pattern.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import nn

from trader.patterns.chart import CHART_PATTERNS, ChartPattern
from trader.patterns.synthetic import NONE, PATTERN_CLASSES, pattern_dataset

logger = logging.getLogger(__name__)
_CHANNELS = ("open", "high", "low", "close")


def encode_window(bars: pd.DataFrame, steps: int = 64) -> np.ndarray:
    """OHLC window as a ``(4, steps)`` array scaled to [0, 1] by the window's low/high range."""
    values = bars.loc[:, list(_CHANNELS)].to_numpy(dtype=float)
    if len(values) < 2:
        raise ValueError("a window needs at least two bars")
    lo, hi = float(np.nanmin(values[:, 2])), float(np.nanmax(values[:, 1]))
    scaled = (values - lo) / (hi - lo) if hi > lo else np.full_like(values, 0.5)
    source = np.linspace(0, 1, len(values))
    target = np.linspace(0, 1, steps)
    return np.stack([np.interp(target, source, scaled[:, c]) for c in range(4)]).astype(np.float32)


class PatternNet(nn.Module):
    """Small 1-D CNN: conv blocks, global average + max pooling, linear head."""

    def __init__(self, n_classes: int, width: int = 32, dropout: float = 0.2) -> None:
        super().__init__()

        def block(c_in: int, c_out: int, kernel: int) -> nn.Sequential:
            return nn.Sequential(
                nn.Conv1d(c_in, c_out, kernel, padding=kernel // 2),
                nn.BatchNorm1d(c_out),
                nn.ReLU(),
            )

        self.features = nn.Sequential(
            block(len(_CHANNELS), width, 7),
            block(width, width, 5),
            nn.MaxPool1d(2),
            block(width, 2 * width, 5),
            block(2 * width, 2 * width, 5),
            nn.MaxPool1d(2),
            block(2 * width, 4 * width, 3),
            block(4 * width, 4 * width, 3),
        )
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(8 * width, n_classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Class logits for a ``(batch, 4, steps)`` input."""
        h = self.features(x)
        pooled = torch.cat([h.mean(dim=-1), h.amax(dim=-1)], dim=-1)
        return self.head(pooled)


@dataclass(frozen=True, slots=True)
class TrainSettings:
    """Training knobs for ``PatternRecognizer.fit``."""

    n_per_class: int = 600
    epochs: int = 15
    batch_size: int = 128
    learning_rate: float = 3e-3
    weight_decay: float = 1e-4
    validation_fraction: float = 0.15
    seed: int = 0


class PatternRecognizer:
    """Chart pattern classifier over OHLC windows."""

    def __init__(
        self, classes: tuple[str, ...] = PATTERN_CLASSES, steps: int = 64, width: int = 32
    ) -> None:
        """Create an untrained recognizer.

        Args:
            classes: Class names; must include ``none``.
            steps: Bars per window after resampling.
            width: Base channel count of the network.

        Raises:
            ValueError: When ``none`` is not a class.
        """
        if NONE not in classes:
            raise ValueError(f"classes must include {NONE!r}")
        self.classes = tuple(classes)
        self.steps = steps
        self.width = width
        self.net = PatternNet(len(self.classes), width)
        self.fitted = False

    def _tensor(self, windows: list[pd.DataFrame]) -> torch.Tensor:
        return torch.from_numpy(np.stack([encode_window(w, self.steps) for w in windows]))

    def fit(
        self,
        settings: TrainSettings | None = None,
        extra: list[tuple[pd.DataFrame, str]] | None = None,
    ) -> dict[str, Any]:
        """Train on synthetic examples, plus optional labelled real windows.

        Args:
            settings: Training settings.
            extra: ``(window, class name)`` pairs, e.g. from ``weak_labels``.

        Returns:
            Validation accuracy overall and per class, and the final training loss.
        """
        settings = settings or TrainSettings()
        torch.manual_seed(settings.seed)
        windows, labels = pattern_dataset(settings.n_per_class, settings.seed, self.classes)
        if extra:
            index = {name: i for i, name in enumerate(self.classes)}
            windows += [w for w, name in extra if name in index]
            labels = np.r_[labels, [index[name] for _, name in extra if name in index]]
        x = self._tensor(windows)
        y = torch.from_numpy(labels.astype(np.int64))
        order = torch.from_numpy(np.random.default_rng(settings.seed).permutation(len(y)))
        n_val = int(len(y) * settings.validation_fraction)
        val_idx, train_idx = order[:n_val], order[n_val:]
        optimizer = torch.optim.AdamW(
            self.net.parameters(), lr=settings.learning_rate, weight_decay=settings.weight_decay
        )
        steps_per_epoch = math.ceil(len(train_idx) / settings.batch_size)
        scheduler = torch.optim.lr_scheduler.OneCycleLR(
            optimizer,
            max_lr=settings.learning_rate,
            total_steps=max(settings.epochs * steps_per_epoch, 1),
        )
        loss_fn = nn.CrossEntropyLoss(label_smoothing=0.05)
        loss_value = float("nan")
        for epoch in range(settings.epochs):
            self.net.train()
            perm = train_idx[torch.randperm(len(train_idx))]
            total = 0.0
            for start in range(0, len(perm), settings.batch_size):
                batch = perm[start : start + settings.batch_size]
                xb = x[batch]
                xb = xb + 0.01 * torch.randn_like(xb)  # light jitter against overfitting
                optimizer.zero_grad()
                loss = loss_fn(self.net(xb), y[batch])
                loss.backward()
                optimizer.step()
                scheduler.step()
                total += float(loss.detach()) * len(batch)
            loss_value = total / max(len(perm), 1)
            logger.info("epoch %d loss %.4f", epoch + 1, loss_value)
        self.fitted = True
        metrics: dict[str, Any] = {"train_loss": loss_value, "n_train": len(train_idx)}
        if n_val:
            pred = self._predict_tensor(x[val_idx]).argmax(axis=1)
            truth = y[val_idx].numpy()
            metrics["val_accuracy"] = float((pred == truth).mean())
            metrics["val_per_class"] = {
                name: float((pred[truth == i] == i).mean())
                for i, name in enumerate(self.classes)
                if (truth == i).any()
            }
        return metrics

    def _predict_tensor(self, x: torch.Tensor) -> np.ndarray:
        self.net.eval()
        with torch.no_grad():
            chunks = [
                torch.softmax(self.net(x[i : i + 1024]), dim=-1) for i in range(0, len(x), 1024)
            ]
        return torch.cat(chunks).numpy() if chunks else np.empty((0, len(self.classes)))

    def predict_proba(self, windows: list[pd.DataFrame]) -> np.ndarray:
        """Class probabilities, one row per window, columns in ``classes`` order.

        Raises:
            RuntimeError: When the model has not been trained or loaded.
        """
        if not self.fitted:
            raise RuntimeError("pattern recognizer is not trained; call fit() or load()")
        if not windows:
            return np.empty((0, len(self.classes)))
        return self._predict_tensor(self._tensor(windows))

    def classify(self, window: pd.DataFrame, top: int = 3) -> list[tuple[str, float]]:
        """The ``top`` most likely classes for one window."""
        probs = self.predict_proba([window])[0]
        best = np.argsort(probs)[::-1][:top]
        return [(self.classes[i], float(probs[i])) for i in best]

    def scan(
        self,
        bars: pd.DataFrame,
        lengths: tuple[int, ...] = (40, 60, 90, 130),
        stride: int = 5,
        threshold: float = 0.6,
        max_overlap: float = 0.5,
    ) -> pd.DataFrame:
        """Slide windows over ``bars`` and keep confident pattern detections.

        Args:
            bars: OHLC history (oldest first).
            lengths: Window lengths in bars.
            stride: Step between window ends.
            threshold: Minimum probability of the best non-``none`` class.
            max_overlap: Overlap (intersection over union) above which a weaker detection is
                dropped in favour of a stronger one.

        Returns:
            Columns ``pattern``, ``direction``, ``probability``, ``start``, ``end`` (bar
            positions) and ``start_time``/``end_time``, strongest first.
        """
        windows: list[pd.DataFrame] = []
        spans: list[tuple[int, int]] = []
        for length in lengths:
            if length > len(bars):
                continue
            ends = list(range(len(bars), length - 1, -stride))
            for end in ends:
                windows.append(bars.iloc[end - length : end])
                spans.append((end - length, end - 1))
        columns = ["pattern", "direction", "probability", "start", "end", "start_time", "end_time"]
        if not windows:
            return pd.DataFrame(columns=columns)
        probs = self.predict_proba(windows)
        none_idx = self.classes.index(NONE)
        probs_no_none = probs.copy()
        probs_no_none[:, none_idx] = -1
        best = probs_no_none.argmax(axis=1)
        best_p = probs[np.arange(len(best)), best]
        keep = np.flatnonzero(best_p >= threshold)
        chosen: list[int] = []
        for i in keep[np.argsort(-best_p[keep])]:
            s, e = spans[i]
            if all(_iou((s, e), spans[j]) <= max_overlap for j in chosen):
                chosen.append(int(i))
        rows = [
            {
                "pattern": self.classes[best[i]],
                "direction": CHART_PATTERNS.get(self.classes[best[i]], 0),
                "probability": float(best_p[i]),
                "start": spans[i][0],
                "end": spans[i][1],
                "start_time": bars.index[spans[i][0]],
                "end_time": bars.index[spans[i][1]],
            }
            for i in chosen
        ]
        return pd.DataFrame(rows, columns=columns)

    def save(self, path: str) -> None:
        """Write weights and settings with ``torch.save``.

        Raises:
            RuntimeError: When the model has not been trained.
        """
        if not self.fitted:
            raise RuntimeError("refusing to save an untrained pattern recognizer")
        torch.save(
            {
                "classes": list(self.classes),
                "steps": self.steps,
                "width": self.width,
                "state_dict": self.net.state_dict(),
            },
            path,
        )

    @classmethod
    def load(cls, path: str) -> PatternRecognizer:
        """Load a recognizer written by ``save``."""
        payload = torch.load(path, map_location="cpu", weights_only=True)
        model = cls(tuple(payload["classes"]), int(payload["steps"]), int(payload["width"]))
        model.net.load_state_dict(payload["state_dict"])
        model.fitted = True
        return model


def _iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]) + 1)
    union = (a[1] - a[0] + 1) + (b[1] - b[0] + 1) - inter
    return inter / union if union else 0.0


def weak_labels(
    bars: pd.DataFrame,
    patterns: list[ChartPattern],
    margin: float = 0.1,
    none_ratio: float = 1.0,
    seed: int = 0,
) -> list[tuple[pd.DataFrame, str]]:
    """Real training windows labelled by the rule detector.

    Each detected pattern contributes its span plus a small ``margin``. About ``none_ratio``
    windows per pattern are also sampled where no pattern was detected and labelled ``none``,
    so the model learns what ordinary real price action looks like.
    """
    out: list[tuple[pd.DataFrame, str]] = []
    covered = np.zeros(len(bars), dtype=bool)
    for p in patterns:
        pad = max(int((p.end - p.start) * margin), 1)
        start, end = max(p.start - pad, 0), min(p.end + pad, len(bars) - 1)
        covered[start : end + 1] = True
        if end - start >= 10:
            out.append((bars.iloc[start : end + 1], p.name))
    rng = np.random.default_rng(seed)
    wanted = int(len(out) * none_ratio)
    for _ in range(20 * wanted):
        if wanted <= 0:
            break
        length = int(rng.integers(30, 160))
        if length >= len(bars):
            continue
        start = int(rng.integers(0, len(bars) - length))
        if not covered[start : start + length].any():
            out.append((bars.iloc[start : start + length], NONE))
            wanted -= 1
    return out
