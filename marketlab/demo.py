"""Deterministic synthetic OHLC bars, never presented as real market data."""

import numpy as np
import pandas as pd

from .data import normalize_bars


def make_demo_bars(rows: int = 600, seed: int = 42) -> pd.DataFrame:
    if rows < 3:
        raise ValueError("Demo requires at least three rows")
    random = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(random.normal(0.0003, 0.005, rows)))
    opening = np.r_[100.0, close[:-1]] * np.exp(random.normal(0, 0.001, rows))
    spread = random.uniform(0.001, 0.007, rows)
    return normalize_bars(pd.DataFrame({
        "DateTime": pd.date_range("2024-01-01 00:00:00", periods=rows, freq="15min"),
        "Open": opening,
        "High": np.maximum(opening, close) * (1 + spread),
        "Low": np.minimum(opening, close) * (1 - spread),
        "Close": close,
        "Volume": np.zeros(rows, dtype=int),
        "TickVolume": random.integers(10, 3000, rows),
    }))
