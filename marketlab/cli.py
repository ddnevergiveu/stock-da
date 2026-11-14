"""Command-line interface. All generated data defaults to artifacts/."""

import argparse
import json
import sqlite3
from pathlib import Path

from .data import quality_summary, read_bars
from .demo import make_demo_bars
from .storage import import_bars
from .strategy import backtest


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_results(result, output: Path, *, plot: bool = False) -> None:
    output.mkdir(parents=True, exist_ok=True)
    result.bars.to_csv(output / "equity.csv", index=False)
    result.trades.to_csv(output / "trades.csv", index=False)
    write_json(output / "metrics.json", result.metrics)
    if plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError as error:
            raise ValueError("Plotting needs matplotlib: pip install '.[charts]'") from error
        import pandas as pd
        dates = pd.to_datetime(result.bars["DateTime"])
        figure, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        axes[0].plot(dates, result.bars["equity"], label="SMA strategy (after costs)")
        axes[0].plot(dates, result.bars["buy_hold_equity"], label="Buy and hold (after costs)", alpha=0.7)
        axes[0].legend()
        axes[0].set_ylabel("Equity")
        axes[0].set_title("Synthetic demo" if result.metrics.get("synthetic") else "Historical research — instrument/timezone unverified")
        axes[1].fill_between(dates, result.bars["drawdown"] * 100, 0, alpha=0.4)
        axes[1].set_ylabel("Drawdown %")
        figure.autofmt_xdate()
        figure.tight_layout()
        figure.savefig(output / "equity.png", dpi=150)
        plt.close(figure)


def add_strategy_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fast", type=int, default=20, help="Fast SMA window in bars")
    parser.add_argument("--slow", type=int, default=60, help="Slow SMA window in bars")
    parser.add_argument("--cost-bps", type=float, default=5, help="Combined fees/slippage per side, in basis points")
    parser.add_argument("--initial-capital", type=float, default=10000)
    parser.add_argument("--periods-per-year", type=float, help="Explicit annualization convention; omitted by default")
    parser.add_argument("--plot", action="store_true", help="Also write equity.png (needs matplotlib)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate market bars, import SQLite, and research an auditable SMA strategy.")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = commands.add_parser("inspect", help="Validate CSV and print quality summary")
    inspect_parser.add_argument("--csv", type=Path, default=Path("15m_data.csv"))
    import_parser = commands.add_parser("import", help="Transactionally import CSV into SQLite")
    import_parser.add_argument("--csv", type=Path, default=Path("15m_data.csv"))
    import_parser.add_argument("--db", type=Path, default=Path("artifacts/market_data.db"))
    import_parser.add_argument("--replace", action="store_true", help="Explicitly replace only market_data in the selected database")
    research_parser = commands.add_parser("backtest", help="Backtest a CSV with next-open execution")
    research_parser.add_argument("--csv", type=Path, default=Path("15m_data.csv"))
    research_parser.add_argument("--output", type=Path, default=Path("artifacts/backtest"))
    add_strategy_options(research_parser)
    demo_parser = commands.add_parser("demo", help="Generate synthetic bars, import them, and run research offline")
    demo_parser.add_argument("--output", type=Path, default=Path("artifacts/demo"))
    demo_parser.add_argument("--rows", type=int, default=600)
    demo_parser.add_argument("--seed", type=int, default=42)
    add_strategy_options(demo_parser)
    args = parser.parse_args(argv)
    try:
        bars = make_demo_bars(args.rows, args.seed) if args.command == "demo" else read_bars(args.csv)
        quality = quality_summary(bars)
        if args.command == "inspect":
            print(json.dumps(quality, ensure_ascii=False, indent=2, allow_nan=False))
        elif args.command == "import":
            print(json.dumps({"database": str(args.db), **import_bars(bars, args.db, replace=args.replace), "quality": quality}, ensure_ascii=False, indent=2, allow_nan=False))
        else:
            result = backtest(bars, fast=args.fast, slow=args.slow, cost_bps=args.cost_bps, initial_capital=args.initial_capital, periods_per_year=args.periods_per_year)
            result.metrics.update({"synthetic": args.command == "demo", "source": "deterministic synthetic data" if args.command == "demo" else str(args.csv), "quality": quality})
            if args.command == "demo":
                args.output.mkdir(parents=True, exist_ok=True)
                result.metrics["seed"] = args.seed
                # Re-running the same demo is idempotent; a different seed requires a new output directory.
                import_bars(bars, args.output / "market_data.db")
                bars.to_csv(args.output / "synthetic_bars.csv", index=False)
            write_results(result, args.output, plot=args.plot)
            print(json.dumps({"output": str(args.output), "synthetic": result.metrics["synthetic"], "rows": len(bars), "completed_trades": result.metrics["completed_trades"], "total_return": result.metrics["total_return"], "max_drawdown": result.metrics["max_drawdown"]}, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, f"error: {error}\n")
    return 0
