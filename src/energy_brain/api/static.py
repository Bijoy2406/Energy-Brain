"""Optional built website, registered after the API and excluded from OpenAPI."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from starlette._utils import get_route_path
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.routing import Match, Mount
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


def _accepts_html(scope: Scope) -> bool:
    for entry in Headers(scope=scope).get("accept", "").lower().split(","):
        media_type, *parameters = entry.strip().split(";")
        if media_type != "text/html":
            continue
        quality = "1"
        for parameter in parameters:
            name, _, value = parameter.strip().partition("=")
            if name == "q":
                quality = value
        try:
            if 0 < float(quality) <= 1:
                return True
        except ValueError:
            pass
    return False


class WebsiteFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or scope["method"] != "GET" or not _accepts_html(scope):
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and scope["method"] == "GET" and _accepts_html(scope):
            return await super().get_response("index.html", scope)
        return response


class WebsiteMount(Mount):
    """Leave API namespaces to the router, including its 404/405/redirect handling."""

    def __init__(self, directory: Path, reserved_roots: set[str]):
        super().__init__("/", app=WebsiteFiles(directory=directory, html=True), name="website")
        self.reserved_roots = reserved_roots

    def matches(self, scope: Scope):
        if scope["type"] == "http":
            root = get_route_path(scope).lstrip("/").split("/", 1)[0]
            if root in self.reserved_roots:
                return Match.NONE, {}
        return super().matches(scope)


def mount_website(app: FastAPI) -> None:
    """Read EB_STATIC_DIR only when creating an app; incomplete builds leave the API alone."""
    configured = os.environ.get("EB_STATIC_DIR")
    if not configured:
        return
    directory = Path(configured).resolve()
    if not directory.is_dir() or not (directory / "index.html").is_file():
        return
    # Reserve the API's top-level namespaces, including docs and a conventional /api prefix.
    reserved_roots = {"plan", "days", "backtest", "explain", "health", "docs", "redoc", "openapi.json", "api"}
    app.router.routes.append(WebsiteMount(directory, reserved_roots))
