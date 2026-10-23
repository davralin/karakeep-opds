#!/usr/bin/env python3
"""Exercise the production image against a local Karakeep API contract stub."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Thread
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

EPUBCHECK_IMAGE = (
    "novalagung/epubcheck:5.3.0@"
    "sha256:8cd5fe34ee461c20e3288d0d946755290bb4187705c7f779fd3f552437c60006"
)


class KarakeepStub(BaseHTTPRequestHandler):
    calls: ClassVar[list[tuple[str, dict[str, list[str]]]]] = []

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self.__class__.calls.append((parsed.path, query))

        if self.headers.get("Authorization") != "Bearer ci-token":
            self._json(401, {"error": "missing bearer token"})
            return
        if self.headers.get("User-Agent") != "karakeep-opds/0.1.0":
            self._json(500, {"error": "unexpected user agent"})
            return

        if parsed.path == "/api/v1/bookmarks":
            if self.headers.get("Accept") != "application/json":
                self._json(500, {"error": "unexpected bookmark accept header"})
                return
            if query.get("limit") != ["2"] or query.get("archived") not in (["false"], ["true"]):
                self._json(500, {"error": "unexpected bookmark query"})
                return
            self._json(200, {"bookmarks": [_bookmark()], "nextCursor": "next"})
            return
        if parsed.path == "/api/v1/bookmarks/abc":
            if self.headers.get("Accept") != "application/json":
                self._json(500, {"error": "unexpected bookmark accept header"})
                return
            self._json(200, _bookmark())
            return
        if parsed.path == "/api/v1/assets/content":
            if self.headers.get("Accept") != "*/*":
                self._json(500, {"error": "unexpected asset accept header"})
                return
            self._bytes(
                200,
                b"<h2>Readable body & chips</h2><p>Line one<br>line two<script>discard()</script>",
                "text/html; charset=utf-8",
            )
            return
        if parsed.path == "/api/v1/assets/cover":
            if self.headers.get("Accept") != "*/*":
                self._json(500, {"error": "unexpected asset accept header"})
                return
            self._bytes(200, b"png", "image/png")
            return
        self._json(404, {"error": "not found"})

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, value: object) -> None:
        self._bytes(status, json.dumps(value).encode(), "application/json")

    def _bytes(self, status: int, value: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value)))
        self.end_headers()
        self.wfile.write(value)


def _bookmark() -> dict[str, object]:
    return {
        "id": "abc",
        "title": "CI bookmark",
        "archived": False,
        "modifiedAt": "2024-01-01T00:00:00Z",
        "tags": [{"name": "ci"}],
        "content": {
            "url": "https://example.test/article",
            "description": "CI description",
            "contentAssetId": "content",
            "imageAssetId": "cover",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--epubcheck-image", default=EPUBCHECK_IMAGE)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("0.0.0.0", 0), KarakeepStub)
    Thread(target=server.serve_forever, daemon=True).start()
    container_id = ""
    try:
        container_id = _run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--add-host=host.docker.internal:host-gateway",
                "--publish",
                "127.0.0.1::8000",
                "--env",
                f"KARAKEEP_BASE_URL=http://host.docker.internal:{server.server_port}",
                "--env",
                "KARAKEEP_API_TOKEN=ci-token",
                "--env",
                "OPDS_USERNAME=reader",
                "--env",
                "OPDS_PASSWORD=ci-password",
                "--env",
                "OPDS_PAGE_SIZE=2",
                args.image,
            ]
        ).stdout.strip()
        port = _run(["docker", "port", container_id, "8000/tcp"]).stdout.strip().rsplit(":", 1)[1]
        base_url = f"http://127.0.0.1:{port}"

        _wait_for_health(base_url)
        _assert_response(base_url + "/opds", expected_status=401)
        atom = _request(base_url + "/opds/bookmarks.atom", auth=True)
        assert b"CI bookmark" in atom
        catalog = _request(base_url + "/opds/archived.json?cursor=next", auth=True)
        assert json.loads(catalog)["publications"][0]["metadata"]["title"] == "CI bookmark"
        epub = _request(base_url + "/opds/bookmarks/abc.epub", auth=True)
        with zipfile.ZipFile(BytesIO(epub)) as archive:
            assert archive.read("mimetype") == b"application/epub+zip"
            assert b"Readable body" in archive.read("OEBPS/article.xhtml")
        _validate_epub(epub, args.epubcheck_image)
        assert _request(base_url + "/opds/assets/cover", auth=True) == b"png"
        _run(
            [
                "docker",
                "exec",
                container_id,
                "python",
                "-c",
                "import urllib.request; "
                "urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()",
            ]
        )
        user = _run(
            ["docker", "inspect", "--format", "{{.Config.User}}", container_id]
        ).stdout.strip()
        assert user == "65532:65532"
        assert ("/api/v1/bookmarks", {"limit": ["2"], "archived": ["false"]}) in KarakeepStub.calls
        assert (
            "/api/v1/bookmarks",
            {"limit": ["2"], "cursor": ["next"], "archived": ["true"]},
        ) in KarakeepStub.calls
        print("production image deployment check passed")
    finally:
        if container_id:
            subprocess.run(["docker", "logs", container_id], check=False)
            subprocess.run(["docker", "stop", container_id], check=False)
        server.shutdown()
        server.server_close()


def _wait_for_health(base_url: str) -> None:
    for _ in range(40):
        try:
            if _request(base_url + "/healthz") == b'{"status":"ok"}':
                return
        except (OSError, URLError):
            pass
        time.sleep(0.25)
    raise RuntimeError("production container did not become healthy")


def _request(url: str, *, auth: bool = False) -> bytes:
    headers = {}
    if auth:
        token = base64.b64encode(b"reader:ci-password").decode()
        headers["Authorization"] = f"Basic {token}"
    with urlopen(Request(url, headers=headers), timeout=5) as response:
        return bytes(response.read())


def _assert_response(url: str, *, expected_status: int) -> None:
    try:
        _request(url)
    except HTTPError as exc:
        assert exc.code == expected_status
        return
    raise AssertionError(f"{url} unexpectedly succeeded")


def _validate_epub(epub: bytes, epubcheck_image: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "bookmark.epub"
        path.write_bytes(epub)
        # This image has a broken shell entrypoint, so invoke its bundled EPUBCheck JAR directly.
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "java",
                "--volume",
                f"{directory}:/data:ro",
                epubcheck_image,
                "-jar",
                "/app/target/epubcheck.jar",
                "/data/bookmark.epub",
            ]
        )


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, subprocess.CalledProcessError, URLError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
