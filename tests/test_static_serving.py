"""Optional website serving needs no data, model, network or LLM key."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from energy_brain.api.app import create_app
from energy_brain.api.settings import ApiSettings


@pytest.fixture
def website(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>Energy Brain</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('demo')", encoding="utf-8")
    # Even a file colliding with an API namespace must never be served.
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "fake.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("EB_STATIC_DIR", str(tmp_path))
    with TestClient(create_app(settings=ApiSettings(warmup=False, data_dir=tmp_path / "missing"))) as client:
        yield client


def test_website_and_asset(website):
    response = website.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == "<html>Energy Brain</html>"
    assert website.get("/assets/app.js").text == "console.log('demo')"


def test_api_and_docs_unchanged(website):
    response = website.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json()["status"] == "ok"
    assert website.get("/docs").status_code == 200
    assert "swagger-ui" in website.get("/docs").text
    exported = json.loads((Path(__file__).resolve().parents[1] / "docs/openapi.json").read_text())
    assert website.get("/openapi.json").json() == exported
    assert website.get("/health/", follow_redirects=False).status_code == 307
    assert website.get("/plan", headers={"Accept": "text/html"}).status_code == 405


@pytest.mark.parametrize("path", [
    "/plan/missing", "/days/missing", "/backtest/missing", "/backtest/days/missing",
    "/explain/missing", "/health/fake.json", "/docs/missing", "/openapi.json/missing", "/api/missing",
    "/report/missing", "/simulate/missing", "/pareto/missing",
])
def test_api_namespaces_never_fall_back_to_html(website, path):
    response = website.get(path, headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"


def test_spa_fallback_only_for_html_get(website):
    assert website.get("/dashboard/day", headers={"Accept": "text/html"}).text == "<html>Energy Brain</html>"
    for accept in ("application/json", "*/*", "text/html;q=0", "text/html;q=0, */*;q=1"):
        assert website.get("/dashboard/day", headers={"Accept": accept}).status_code == 404
    assert website.post("/dashboard/day", headers={"Accept": "text/html"}).status_code == 405
    assert website.head("/dashboard/day", headers={"Accept": "text/html"}).status_code == 404


def test_spa_fallback_with_custom_static_404_page(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>Energy Brain</html>", encoding="utf-8")
    (tmp_path / "404.html").write_text("<html>Missing</html>", encoding="utf-8")
    monkeypatch.setenv("EB_STATIC_DIR", str(tmp_path))
    with TestClient(create_app(settings=ApiSettings(warmup=False))) as client:
        response = client.get("/dashboard", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert response.text == "<html>Energy Brain</html>"
        assert client.get("/dashboard", headers={"Accept": "application/json"}).status_code == 404


@pytest.mark.parametrize("setting", [None, "missing", "no-index"])
def test_static_dir_disabled_or_incomplete(tmp_path, monkeypatch, setting):
    monkeypatch.delenv("EB_STATIC_DIR", raising=False)
    if setting:
        path = tmp_path / setting
        if setting == "no-index":
            path.mkdir()
        monkeypatch.setenv("EB_STATIC_DIR", str(path))
    with TestClient(create_app(settings=ApiSettings(warmup=False))) as client:
        assert client.get("/").status_code == 404
