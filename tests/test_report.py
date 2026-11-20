"""Monthly CFO report: real evaluator rows, no solver, network or wall clock."""

import copy
import io
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from energy_brain.api.app import create_app
from energy_brain.api.service import PlanService, RateLimiter
from energy_brain.api.settings import ApiSettings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def report():
    return json.loads((ROOT / "reports/backtest.json").read_text())


@pytest.fixture
def service():
    return PlanService(ApiSettings(warmup=False))


def pdf_text(content):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    assert 2 <= len(reader.pages) <= 3
    assert all(float(p.mediabox.width) == pytest.approx(595.28, abs=0.1) for p in reader.pages)
    return "\n".join(p.extract_text() for p in reader.pages)


def test_report_pdf_headers_content_and_reproducibility(service, report):
    with TestClient(create_app(service)) as client:
        response = client.get("/report?month=2024-07&weight=0.5")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == (
            'attachment; filename="gridpilot-report-2024-07.pdf"'
        )
        assert response.content.startswith(b"%PDF")
        assert client.get("/report?month=2024-07&weight=0.5").content == response.content
    text = pdf_text(response.content)
    flattened = " ".join(text.split())
    for name in report["headline"]["baseline_names"].values():
        assert name in text
    assert "Historical replay on synthetic data" in text
    assert "not a live deployment" in text
    assert "not a guarantee of future savings" in flattened
    assert "2024-07" in text
    assert "31 included" in text
    # Joining lines only removes PDF layout wrapping, not caveat words or punctuation.
    for caveat in report["caveats"]:
        assert " ".join(caveat.split()) in flattened
    assert "dfb9cbd0bf29" in text
    assert report["config"]["data_sha256"]["load"] not in text
    # A fresh renderer, after removing the byte cache, must still be deterministic.
    from energy_brain.api.report import render_report, report_data

    data = report_data(report, "2024-07", 0.5)
    assert render_report(data) == response.content == render_report(data)


def test_month_uses_only_included_rows_at_selected_weight(report):
    from energy_brain.api.report import report_data

    data = report_data(report, "2024-07", 0)
    rows = [d for d in report["days"] if d["date"].startswith("2024-07") and d["included"]]
    assert len(data.daily) == len(rows) == 31
    assert data.outage_days == sum(d["outage_slots"] > 0 for d in rows)
    for method in ("optimiser", "baseline_solar_first", "baseline_time_of_use"):
        for field in (
            "adjusted_cost_bdt",
            "adjusted_co2_kg",
            "generator_hours",
            "diesel_litres",
            "unserved_kwh",
        ):
            expected = sum(
                r[field]
                for d in rows
                for r in d["methods"]
                if r["method"] == method and (method != "optimiser" or r["weight"] == 0)
            )
            assert data.totals[method][field] == pytest.approx(expected)
    changed = copy.deepcopy(report)
    changed["days"][182]["included"] = False  # July 1 in leap year
    changed["days"][182]["excluded_reason"] = "test exclusion"
    excluded = report_data(changed, "2024-07", 0)
    assert len(excluded.daily) == 30
    from energy_brain.api.report import render_report

    text = pdf_text(render_report(excluded))
    assert "2024-07-01: test exclusion" in text
    assert "30 included" in text


def test_all_excluded_month_keeps_reasons_above_footer(report):
    from pypdf import PdfReader

    from energy_brain.api.report import render_report, report_data

    reason = "actual PV or load incomplete for this day"
    for day in report["days"]:
        if day["date"].startswith("2024-07"):
            day.update(included=False, excluded_reason=reason, methods=[])
    content = render_report(report_data(report, "2024-07"))
    reader = PdfReader(io.BytesIO(content))
    positions = []

    def visit(text, cm, tm, font, size):
        if text.strip() and not text.startswith("Gridpilot AI |") and text.strip() != "1 / 3":
            positions.append(tm[5])

    text = reader.pages[0].extract_text(visitor_text=visit)
    assert "0 included; 31 excluded" in text
    assert reason in text
    assert "2024-07-01" in text and "2024-07-31" in text
    assert min(positions) >= 42  # leave room for footer; no clipped exclusion text


@pytest.mark.parametrize("weight", [None, 0, 1])
def test_full_year_pdf_numbers_match_summary_and_headline(report, service, weight):
    from energy_brain.api.report import report_data

    data = report_data(report, None, weight)
    w = 0.5 if weight is None else weight
    summary = report["summary"]["by_weight"][f"{w:g}"]
    with TestClient(create_app(service)) as client:
        response = client.get("/report", params={} if weight is None else {"weight": weight})
    assert response.status_code == 200
    text = pdf_text(response.content)
    assert "2024 full year" in text
    for method, totals in data.totals.items():
        for field, value in totals.items():
            assert value == summary["totals"][method][field]
            assert f"{value:,.2f}" in text
    for baseline, values in summary["vs"].items():
        for field in ("cost_saved_bdt", "cost_saved_pct"):
            value = values[field]
            assert f"{value:+,.2f}" in text if field.endswith("bdt") else f"{value:+.3f}%" in text
        assert f"{-values['co2_saved_kg']:+,.2f}" in text
        assert f"{-values['co2_saved_pct']:+.3f}%" in text
        if weight is None:
            # Headline rounds BDT/kg to integers, percentages to one decimal.
            line = next(
                s
                for s in report["headline"]["lines"]
                if s.startswith(f"vs the {report['headline']['baseline_names'][baseline]}:")
            )
            for amount in (values["cost_saved_bdt"], abs(values["co2_saved_kg"])):
                assert f"{amount:,.0f}" in line
            for pct in (values["cost_saved_pct"], abs(values["co2_saved_pct"])):
                assert f"{pct:.1f} %" in line
    if weight is None:
        assert re.search(r"CO2 increase: \+8,388\.\d{2} kg \(\+0\.317%\)", text)


@pytest.mark.parametrize("month", ["2023-12", "2025-01", "2024-00", "2024-13"])
def test_report_month_not_in_backtest(service, month):
    with TestClient(create_app(service)) as client:
        response = client.get("/report", params={"month": month})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "month_not_in_backtest"
    assert "2024-01 .. 2024-12" in response.json()["detail"]["message"]


@pytest.mark.parametrize("query", ["month=July", "month=2024-7", "month=2024-07-01", "weight=abc"])
def test_report_malformed_query_uses_validation(service, query):
    with TestClient(create_app(service)) as client:
        response = client.get(f"/report?{query}")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("weight", [0.25, -1, 2])
def test_report_rejects_weight_not_run(service, weight):
    with TestClient(create_app(service)) as client:
        response = client.get("/report", params={"weight": weight})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "weight_not_in_backtest"


def test_report_missing_backtest(tmp_path):
    service = PlanService(ApiSettings(warmup=False, backtest_path=tmp_path / "missing.json"))
    with TestClient(create_app(service)) as client:
        response = client.get("/report")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "backtest_unavailable"


def test_report_rate_limit_includes_cached_downloads_and_recovers(service):
    now = [0.0]
    service.report_limiter = RateLimiter(2, 3, clock=lambda: now[0])
    with TestClient(create_app(service)) as client:
        assert client.get("/report").status_code == 200
        assert client.get("/report").status_code == 200
        response = client.get("/report")
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "rate_limited"
        assert int(response.headers["retry-after"]) > 0
        now[0] = 61
        assert client.get("/report").status_code == 200


def test_report_lru_reuses_bytes_and_evicts_least_recent(service, monkeypatch):
    import energy_brain.api.report as module
    from energy_brain.api.service import REPORT_CACHE_SIZE

    calls = []
    original = module.render_report

    def counted(data):
        calls.append((data.month, data.weight))
        return original(data)

    monkeypatch.setattr(module, "render_report", counted)
    raw = service.backtest()
    first = service.report_pdf(raw, "2024-01", 0.5)
    service.report_pdf(raw, "2024-02", 0.5)
    assert service.report_pdf(raw, "2024-01", 0.5) is first
    keys = [
        (f"2024-{m:02}", w)
        for w in (0, 1, 0.5)
        for m in range(1, 13)
        if (f"2024-{m:02}", w) not in (("2024-01", 0.5), ("2024-02", 0.5))
    ]
    for month, weight in keys[: REPORT_CACHE_SIZE - 2]:
        service.report_pdf(raw, month, weight)
    service.report_pdf(raw, "2024-01", 0.5)  # refresh January, February is now oldest
    service.report_pdf(raw, None, 0.5)
    assert service.report_pdf(raw, "2024-01", 0.5) is first
    before = len(calls)
    service.report_pdf(raw, "2024-02", 0.5)
    assert len(calls) == before + 1
