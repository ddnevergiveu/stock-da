import importlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from marketlab.data import normalize_bars, read_bars
from marketlab.demo import make_demo_bars
from marketlab.storage import import_bars
from marketlab.strategy import backtest


def known_bars():
    closing = np.array([100, 100, 110, 120, 130, 140], dtype=float)
    opening = np.array([100, 100, 100, 120, 130, 140], dtype=float)
    return pd.DataFrame({
        "DateTime": pd.date_range("2024-01-01", periods=6, freq="15min"),
        "Open": opening, "High": np.maximum(opening, closing),
        "Low": np.minimum(opening, closing), "Close": closing,
        "Volume": 0, "TickVolume": 100,
    })


class ValidationTests(unittest.TestCase):
    def test_reverse_order_and_legacy_timestamps(self):
        bars = known_bars()
        bars["DateTime"] = bars["DateTime"].dt.strftime("%Y.%m.%d %H:%M:%S")
        result = normalize_bars(bars.iloc[::-1])
        self.assertEqual(result.iloc[0]["DateTime"], "2024-01-01 00:00:00")
        self.assertEqual(result.iloc[-1]["Close"], 140)

    def test_exact_duplicates_removed_conflicts_rejected(self):
        bars = known_bars()
        self.assertEqual(len(normalize_bars(pd.concat([bars, bars.iloc[:1]]))), 6)
        conflicting = bars.iloc[:1].copy()
        conflicting["TickVolume"] = 50
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            normalize_bars(pd.concat([bars, conflicting]))

    def test_invalid_values_rejected(self):
        for column, value in [("Open", np.inf), ("Close", 0), ("High", 1), ("Low", 200), ("Volume", -1), ("TickVolume", 2.5), ("DateTime", "2024-01-01 00:00:00+08:00")]:
            with self.subTest(column=column, value=value):
                bars = known_bars().astype({column: object})
                bars.loc[0, column] = value
                with self.assertRaises(ValueError):
                    normalize_bars(bars)

    def test_bad_date_and_empty_rejected(self):
        bars = known_bars().astype({"DateTime": object})
        bars.loc[0, "DateTime"] = "2024-02-31 00:00:00"
        with self.assertRaises(ValueError):
            normalize_bars(bars)
        with self.assertRaises(ValueError):
            normalize_bars(bars.iloc[:0])

    def test_csv_and_tsv_have_same_meaning(self):
        with tempfile.TemporaryDirectory() as directory:
            comma, tab = Path(directory) / "comma.csv", Path(directory) / "tab.csv"
            known_bars().to_csv(comma, index=False)
            known_bars().to_csv(tab, sep="\t", index=False)
            pd.testing.assert_frame_equal(read_bars(comma), read_bars(tab))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "market.db"

    def test_idempotent_and_incremental(self):
        bars = known_bars()
        self.assertEqual(import_bars(bars.iloc[:3], self.database)["inserted"], 3)
        report = import_bars(bars, self.database)
        self.assertEqual(report["inserted"], 3)
        self.assertEqual(report["skipped_identical"], 3)
        self.assertEqual(import_bars(bars, self.database)["inserted"], 0)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_data").fetchone()[0], 6)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_data WHERE STRFTIME('%w', DateTime) IS NULL").fetchone()[0], 0)

    def test_conflict_rolls_back_entire_batch(self):
        bars = known_bars()
        import_bars(bars.iloc[:3], self.database)
        bars.loc[0, "TickVolume"] = 999
        with self.assertRaisesRegex(ValueError, "Conflicting existing"):
            import_bars(bars, self.database)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM market_data").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT TickVolume FROM market_data ORDER BY DateTime LIMIT 1").fetchone()[0], 100)

    def test_old_schema_needs_explicit_replace_and_keeps_other_tables(self):
        with sqlite3.connect(self.database) as connection:
            known_bars().to_sql("market_data", connection, index=False)
            connection.execute("CREATE TABLE notes (text TEXT)")
            connection.execute("INSERT INTO notes VALUES ('keep me')")
        with self.assertRaisesRegex(ValueError, "incompatible"):
            import_bars(known_bars(), self.database)
        report = import_bars(known_bars().iloc[:2], self.database, replace=True)
        self.assertEqual(report["replaced_rows"], 6)
        self.assertEqual(report["total_rows"], 2)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT text FROM notes").fetchone()[0], "keep me")


class StrategyTests(unittest.TestCase):
    def test_next_open_not_current_close_execution(self):
        result = backtest(known_bars(), fast=1, slow=2, cost_bps=0)
        buy = result.trades.iloc[0]
        self.assertEqual(buy["DateTime"], "2024-01-01 00:45:00")
        self.assertEqual(buy["price"], 120)
        self.assertAlmostEqual(result.metrics["total_return"], 140 / 120 - 1)
        self.assertEqual(result.trades.iloc[-1]["execution"], "final_close")

    def test_entry_and_final_exit_costs(self):
        result = backtest(known_bars(), fast=1, slow=2, cost_bps=10)
        expected = 10000 / (120 * 1.001) * 140 * 0.999
        self.assertAlmostEqual(result.metrics["final_equity"], expected)
        self.assertAlmostEqual(result.metrics["total_cost_paid"], result.trades["cost"].sum())

    def test_opening_gaps_are_charged_to_existing_holdings(self):
        bars = known_bars()
        # Buy at 120 on bar 3. A later close below the SMA creates an exit
        # signal, which can only fill after a gap down at bar 5's open (80).
        bars.loc[4, ["Open", "High", "Low", "Close"]] = [130, 130, 100, 100]
        bars.loc[5, ["Open", "High", "Low", "Close"]] = [80, 90, 80, 90]
        result = backtest(bars, fast=1, slow=2, cost_bps=0)
        self.assertEqual(result.trades.iloc[-1]["price"], 80)
        self.assertEqual(result.trades.iloc[-1]["execution"], "next_open")
        self.assertAlmostEqual(result.metrics["final_equity"], 10000 * 80 / 120)

    def test_future_changes_cannot_rewrite_past(self):
        bars = make_demo_bars(200)
        first = backtest(bars, fast=5, slow=15)
        modified = bars.copy()
        modified.loc[100:, ["Open", "High", "Low", "Close"]] *= 3
        second = backtest(modified, fast=5, slow=15)
        pd.testing.assert_frame_equal(first.bars.iloc[:100], second.bars.iloc[:100])

    def test_no_trade_flat_prices_and_annualization_opt_in(self):
        bars = known_bars()
        bars[["Open", "High", "Low", "Close"]] = 100.0
        result = backtest(bars, fast=1, slow=2)
        self.assertTrue(result.trades.empty)
        self.assertEqual(result.metrics["total_return"], 0)
        self.assertEqual(result.metrics["max_drawdown"], 0)
        self.assertIsNone(result.metrics["annualized_volatility"])
        self.assertLess(result.metrics["buy_hold_return_after_costs"], 0)
        annual = backtest(bars, fast=1, slow=2, periods_per_year=1000)
        self.assertEqual(annual.metrics["annualized_volatility"], 0)
        self.assertIsNone(annual.metrics["sharpe_zero_risk_free"])

    def test_invalid_strategy_parameters(self):
        for options in [{"fast": 2, "slow": 2}, {"fast": 0}, {"cost_bps": -1}, {"cost_bps": float("nan")}, {"periods_per_year": 0}, {"initial_capital": 0}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                backtest(known_bars(), **options)


class IntegrationTests(unittest.TestCase):
    def test_demo_cli_and_repeat_are_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-m", "marketlab", "demo", "--output", directory, "--rows", "100", "--fast", "5", "--slow", "15"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            path = Path(directory) / "metrics.json"
            original = path.read_text()
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(path.read_text(), original)
            self.assertTrue(json.loads(original)["synthetic"])
            changed = subprocess.run(command + ["--seed", "43"], capture_output=True, text=True)
            self.assertEqual(changed.returncode, 2)
            self.assertEqual(path.read_text(), original)

    def test_import_compatibility_module_has_no_side_effects(self):
        module = importlib.import_module("import_to_sqlite")
        self.assertTrue(callable(module.main))

    def test_all_documented_sql_queries_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "sample.db"
            import_bars(known_bars(), database)
            script = (Path(__file__).resolve().parents[1] / "queries.sql").read_text(encoding="utf-8")
            with sqlite3.connect(database) as connection:
                query = ""
                for line in script.splitlines(keepends=True):
                    query += line
                    if sqlite3.complete_statement(query):
                        connection.execute(query).fetchall()
                        query = ""


if __name__ == "__main__":
    unittest.main()
