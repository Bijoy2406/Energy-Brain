"""Rerun the backtest (#20): ``python -m energy_brain.evaluator``.

Defaults: every day of 2024 (out-of-sample), weights 0.5 (headline), 1 and 0, reports in ``reports/``.
Needs ``data/processed/{pv,load,weather}.parquet`` and ``models/`` (built locally, see
``docs/data-sources.md``); refuses to run without them, because a backtest on fallback forecasts or sample
data is not a pitch number. Deterministic: the same command gives the same numbers (only ``run`` timings
in the JSON differ).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

from energy_brain.evaluator.backtest import Actuals, run_backtest, select_days
from energy_brain.evaluator.report import build_report, collect_config, write_csv, write_json

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WEIGHTS = (0.5, 1.0, 0.0)


def _test_years(root: Path) -> tuple[int, list[int]]:
    """Test and train years from ``reports/forecast_metrics.json`` (2024 / 2022-2023 if absent)."""
    try:
        solar = json.loads((root / "reports" / "forecast_metrics.json").read_text())["solar"]
        return int(solar["test_year"]), [int(y) for y in solar["train_years"]]
    except (OSError, KeyError, ValueError):
        return 2024, [2022, 2023]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m energy_brain.evaluator", description=__doc__.splitlines()[0])
    p.add_argument(
        "--start", type=date.fromisoformat, default=None, help="first day (default: Jan 1 of test year)"
    )
    p.add_argument(
        "--end", type=date.fromisoformat, default=None, help="last day (default: Dec 31 of test year)"
    )
    p.add_argument("--days", type=int, default=None, help="pick N evenly spaced days in [start, end]")
    p.add_argument(
        "--weight",
        type=float,
        action="append",
        default=None,
        help="weight w in [0, 1]; repeat for several, the first is the headline (default: 0.5 1 0)",
    )
    p.add_argument("--out", type=Path, default=ROOT / "reports", help="output directory (default: reports/)")
    p.add_argument("--data-dir", type=Path, default=ROOT / "data", help="data directory (default: data/)")
    p.add_argument("--models-dir", type=Path, default=ROOT / "models", help="models directory")
    p.add_argument(
        "--allow-in-sample",
        action="store_true",
        help="allow days outside the test year (flagged in the report)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
    test_year, train_years = _test_years(ROOT)
    first = args.start or date(test_year, 1, 1)
    last = args.end or date(test_year, 12, 31)
    weights = args.weight or list(DEFAULT_WEIGHTS)
    days = select_days(first, last, args.days)
    in_sample = [d for d in days if d.year != test_year]
    if in_sample and not args.allow_in_sample:
        print(
            f"error: {len(in_sample)} day(s) outside the out-of-sample test year {test_year} "
            f"(models trained on {train_years}); use --allow-in-sample to run anyway",
            file=sys.stderr,
        )
        return 2

    processed = args.data_dir / "processed"
    files = {k: processed / f"{k}.parquet" for k in ("pv", "load", "weather")}
    missing = [str(p) for p in files.values() if not p.is_file()]
    models = sorted(args.models_dir.glob("*_p[159]0.txt")) if args.models_dir.is_dir() else []
    if missing or len(models) < 6:
        print(
            "error: the backtest needs data/processed/{pv,load,weather}.parquet and the six models/*.txt "
            f"(missing data: {missing or 'none'}, models found: {len(models)}). See docs/data-sources.md.",
            file=sys.stderr,
        )
        return 2

    # Real agents only; imported here so nothing is read at import time.
    from energy_brain.config import load_site_config
    from energy_brain.defaults import build_default_agents
    from energy_brain.forecast import ModelForecastAgent

    pv_df, load_df = pd.read_parquet(files["pv"]), pd.read_parquet(files["load"])
    weather_df = pd.read_parquet(files["weather"])
    cfg = load_site_config()
    agents = build_default_agents(
        cfg,
        forecast_agent=ModelForecastAgent(
            weather_df=weather_df, pv_df=pv_df, load_df=load_df, models_dir=args.models_dir, config=cfg
        ),
    )
    actuals = Actuals.from_frames(pv_df, load_df)

    def progress(i: int, n: int, day: str) -> None:
        if i == n or i % 10 == 0:
            print(f"  {i}/{n} days ({day})", file=sys.stderr, flush=True)

    print(f"Backtest {days[0]}..{days[-1]} ({len(days)} days), weights {weights}", file=sys.stderr)
    t0 = time.perf_counter()
    records = run_backtest(days, weights, agents, actuals, progress=progress)
    runtime = time.perf_counter() - t0

    config = collect_config(
        days=days,
        weights=weights,
        headline_weight=weights[0],
        root=ROOT,
        data_files=files,
        models_dir=args.models_dir,
        signal_provenance=agents["signal_agent"].provenance(),
        test_year=test_year,
        train_years=train_years,
        data_source="processed",
    )
    run = {
        "runtime_seconds": runtime,
        "seconds_per_day": runtime / max(1, len(days)),
        "note": "wall-clock timings vary between runs; every other number is deterministic",
    }
    report = build_report(records, weights, weights[0], config, run)
    args.out.mkdir(parents=True, exist_ok=True)
    write_json(report, args.out / "backtest.json")
    write_csv(report, args.out / "backtest.csv")
    for line in report["headline"]["lines"]:
        print(line)
    print(f"Wrote {args.out / 'backtest.json'} and {args.out / 'backtest.csv'} in {runtime:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
