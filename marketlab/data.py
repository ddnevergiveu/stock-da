"""Input validation shared by CSV import and strategy research."""

import csv
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["DateTime", "Open", "High", "Low", "Close", "Volume", "TickVolume"]
PRICES = ["Open", "High", "Low", "Close"]


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate one instrument, reject conflicting timestamps, return sorted bars.

    Timezone is deliberately not inferred. Accepted input is a naive timestamp
    at whole-second resolution, with YYYY.MM.DD or YYYY-MM-DD date notation.
    """
    missing = set(COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("No market data rows supplied")
    result = frame[COLUMNS].copy()
    text = result["DateTime"].astype(str).str.strip()
    valid = text.str.fullmatch(r"\d{4}[-.]\d{2}[-.]\d{2}[ T]\d{2}:\d{2}:\d{2}")
    if not valid.all():
        raise ValueError("DateTime must be naive YYYY-MM-DD HH:MM:SS (or YYYY.MM.DD); timezone is not inferred")
    normalized = text.str.replace(".", "-", regex=False).str.replace("T", " ", regex=False)
    parsed = pd.to_datetime(normalized, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    if parsed.isna().any():
        raise ValueError("DateTime contains an invalid date or time")
    result["DateTime"] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S")
    for column in PRICES + ["Volume", "TickVolume"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
        if not np.isfinite(result[column].to_numpy(dtype=float)).all():
            raise ValueError(f"{column} must contain finite numeric values")
    if (result[PRICES] <= 0).any().any():
        raise ValueError("OHLC prices must be strictly positive")
    if (result["High"] < result[["Open", "Close", "Low"]].max(axis=1)).any() or (
        result["Low"] > result[["Open", "Close", "High"]].min(axis=1)
    ).any():
        raise ValueError("Invalid OHLC range: Low <= Open/Close <= High is required")
    for column in ["Volume", "TickVolume"]:
        values = result[column]
        if ((values < 0) | (values % 1 != 0) | (values >= 2**63)).any():
            raise ValueError(f"{column} must be a nonnegative signed-64-bit integer")
        result[column] = values.astype("int64")
    result[PRICES] = result[PRICES].astype(float)
    result = result.drop_duplicates()
    if result["DateTime"].duplicated().any():
        raise ValueError("Conflicting rows share a DateTime; one database represents one instrument")
    return result.sort_values("DateTime", kind="stable").reset_index(drop=True)


def read_bars(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        header = handle.readline()
    try:
        delimiter = csv.Sniffer().sniff(header, delimiters=",\t;").delimiter
    except csv.Error as error:
        raise ValueError("Cannot detect CSV delimiter; use comma, tab, or semicolon") from error
    return normalize_bars(pd.read_csv(path, sep=delimiter, encoding="utf-8-sig"))


def quality_summary(bars: pd.DataFrame) -> dict:
    dates = pd.to_datetime(bars["DateTime"])
    intervals = dates.diff().dt.total_seconds().dropna()
    warnings = ["Instrument, exchange, and timezone are not inferred from prices."]
    if bars["Volume"].eq(0).all():
        warnings.append("Volume is zero throughout; TickVolume is a tick count, not traded volume.")
    if intervals.nunique() > 1:
        warnings.append("Irregular gaps are retained; missing bars are not forward-filled.")
    if len(intervals) and intervals.max() > 3 * 86400:
        warnings.append("A gap exceeds three days; missing observations may hide trades and bias research results.")
    largest = int(intervals.idxmax()) if len(intervals) else None
    return {
        "rows": int(len(bars)),
        "start": bars["DateTime"].iloc[0],
        "end": bars["DateTime"].iloc[-1],
        "median_interval_seconds": float(intervals.median()) if len(intervals) else None,
        "max_gap_seconds": float(intervals.max()) if len(intervals) else None,
        "max_gap_start": bars["DateTime"].iloc[largest - 1] if largest is not None else None,
        "max_gap_end": bars["DateTime"].iloc[largest] if largest is not None else None,
        "intervals_over_15_minutes": int((intervals > 900).sum()),
        "warnings": warnings,
    }
