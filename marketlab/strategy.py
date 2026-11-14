"""Long/cash moving-average crossover, executed at the following bar's open."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import normalize_bars


@dataclass
class BacktestResult:
    bars: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict


def backtest(
    frame: pd.DataFrame,
    *,
    fast: int = 20,
    slow: int = 60,
    cost_bps: float = 5.0,
    initial_capital: float = 10_000.0,
    periods_per_year: float | None = None,
) -> BacktestResult:
    """Use only completed bars; account for gaps, per-side costs, and final exit.

    Fractional units and execution at the next available open are assumptions,
    not claims about actual fillability. No leverage, shorting, or interest.
    """
    if not isinstance(fast, int) or not isinstance(slow, int) or not 1 <= fast < slow:
        raise ValueError("Windows must be integers satisfying 1 <= fast < slow")
    if not np.isfinite(cost_bps) or not 0 <= cost_bps < 10_000:
        raise ValueError("cost_bps must be finite and between 0 (inclusive) and 10000")
    if not np.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("initial_capital must be finite and positive")
    if periods_per_year is not None and (not np.isfinite(periods_per_year) or periods_per_year <= 0):
        raise ValueError("periods_per_year must be finite and positive")
    bars = normalize_bars(frame)
    if len(bars) < slow + 2:
        raise ValueError(f"At least {slow + 2} bars required for this slow window")
    bars["fast_sma"] = bars["Close"].rolling(fast, min_periods=fast).mean()
    bars["slow_sma"] = bars["Close"].rolling(slow, min_periods=slow).mean()
    bars["signal_at_close"] = (bars["fast_sma"] > bars["slow_sma"]).astype(int)
    bars["position_at_open"] = bars["signal_at_close"].shift(1, fill_value=0)
    cost = cost_bps / 10_000
    cash, units, entry_capital = float(initial_capital), 0.0, 0.0
    fees = 0.0
    equity, transactions = [], []
    for row in bars.itertuples(index=False):
        if row.position_at_open and units == 0:
            entry_capital = cash
            units = cash / (row.Open * (1 + cost))
            paid = units * row.Open * cost
            fees += paid
            cash = 0.0
            transactions.append({"DateTime": row.DateTime, "side": "BUY", "execution": "next_open", "price": row.Open, "units": units, "cost": paid, "round_trip_return": None})
        elif not row.position_at_open and units > 0:
            paid = units * row.Open * cost
            fees += paid
            cash = units * row.Open - paid
            transactions.append({"DateTime": row.DateTime, "side": "SELL", "execution": "next_open", "price": row.Open, "units": units, "cost": paid, "round_trip_return": cash / entry_capital - 1})
            units = 0.0
        equity.append(cash + units * row.Close)
    if units > 0:
        last = bars.iloc[-1]
        paid = units * last["Close"] * cost
        fees += paid
        cash = units * last["Close"] - paid
        transactions.append({"DateTime": last["DateTime"], "side": "SELL", "execution": "final_close", "price": last["Close"], "units": units, "cost": paid, "round_trip_return": cash / entry_capital - 1})
        equity[-1] = cash
    bars["equity"] = equity
    bars["bar_return"] = bars["equity"].div(bars["equity"].shift(1, fill_value=initial_capital)) - 1
    high_water = np.maximum.accumulate(np.r_[initial_capital, bars["equity"].to_numpy()])[1:]
    bars["drawdown"] = bars["equity"] / high_water - 1
    benchmark_units = initial_capital / (bars["Open"].iloc[0] * (1 + cost))
    bars["buy_hold_equity"] = benchmark_units * bars["Close"]
    bars.loc[bars.index[-1], "buy_hold_equity"] *= 1 - cost
    trade_columns = ["DateTime", "side", "execution", "price", "units", "cost", "round_trip_return"]
    trades = pd.DataFrame(transactions, columns=trade_columns)
    completed = trades.loc[trades["side"].eq("SELL"), "round_trip_return"]
    metrics = {
        "strategy": "long_cash_sma_next_open",
        "fast_window": fast,
        "slow_window": slow,
        "cost_bps_per_side": float(cost_bps),
        "initial_capital": float(initial_capital),
        "final_equity": float(bars["equity"].iloc[-1]),
        "total_return": float(bars["equity"].iloc[-1] / initial_capital - 1),
        "buy_hold_return_after_costs": float(bars["buy_hold_equity"].iloc[-1] / initial_capital - 1),
        "max_drawdown": float(bars["drawdown"].min()),
        "completed_trades": len(completed),
        "trade_fills": len(trades),
        "win_rate_after_costs": float((completed > 0).mean()) if len(completed) else None,
        "total_cost_paid": float(fees),
        "bar_exposure_fraction": float(bars["position_at_open"].mean()),
        "periods_per_year": periods_per_year,
        "annualized_volatility": None,
        "sharpe_zero_risk_free": None,
        "assumptions": [
            "Signal uses bar t close; orders execute at bar t+1 open, including any calendar gap.",
            "Single instrument, long or cash, fractional units, no leverage, no cash interest.",
            "Proportional fees and slippage are combined in cost_bps_per_side; all positions close at final close.",
            "Benchmark buys at the first open and exits at the final close, with the same per-side costs.",
            "Exposure is a fraction of bars, not calendar time; annualization requires an explicit convention.",
            "Maximum drawdown uses bar-close equity plus initial capital; intrabar drawdown is not measured.",
            "No spread, impact, liquidity, taxes, financing, corporate actions, or optimized parameter claims.",
        ],
    }
    if periods_per_year is not None:
        returns = bars["bar_return"]
        deviation = float(returns.std(ddof=1))
        metrics["annualized_volatility"] = deviation * np.sqrt(periods_per_year)
        metrics["sharpe_zero_risk_free"] = float(returns.mean() / deviation * np.sqrt(periods_per_year)) if deviation > 0 else None
    return BacktestResult(bars, trades, metrics)
