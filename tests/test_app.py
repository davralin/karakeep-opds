from __future__ import annotations

import base64
import zipfile
from io import BytesIO
from typing import Any

from fastapi.testclient import TestClient

from karakeep_opds.app import create_app, get_client
from karakeep_opds.config import Settings, get_settings
from karakeep_opds.models import Asset, Bookmark, BookmarkPage


class FakeKarakeepClient:
    calls: list[bool | None] = []

    async def list_bookmarks(
        self,
        *,
        limit: int,
        cursor: str = "",
        archived: bool | None = None,
    ) -> BookmarkPage:
        assert limit == 2
        assert cursor in {"", "next"}
        self.calls.append(archived)
        return BookmarkPage(
            bookmarks=(
                Bookmark(
                    id="abc",
                    title="Example",
                    url="https://example.test/article",
                    description="Description",
                    modified_at="2024-01-01T00:00:00Z",
                    image_asset_id="cover",
                    tags=("tag",),
                ),
            ),
            next_cursor="next" if not cursor else "",
        )

    async def get_bookmark(self, bookmark_id: str) -> Bookmark:
        assert bookmark_id == "abc"
        return Bookmark(
            id="abc",
            title="Example",
            description="Summary must not be used as body",
            modified_at="2024-01-01T00:00:00Z",
            assets=(Asset(id="content", asset_type="linkHtmlContent"),),
        )

    async def get_asset(self, asset_id: str) -> tuple[bytes, str]:
        if asset_id == "content":
            return b"<p>Asset body</p>", "text/html"
        return b"image", "image/png"


def test_healthz_without_auth() -> None:
    client = _test_client()

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_opds_requires_auth() -> None:
    client = _test_client()

    response = client.get("/opds")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_root_serves_navigation_feed() -> None:
    client = _test_client()

    response = client.get("/", headers=_auth_header())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/atom+xml")
    assert "Bookmarks/" in response.text
    assert "Archived/" in response.text


def test_bookmarks_atom() -> None:
    FakeKarakeepClient.calls.clear()
    client = _test_client()

    response = client.get("/opds/bookmarks.atom", headers=_auth_header())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/atom+xml")
    assert "Example" in response.text
    assert "/opds/bookmarks/abc.epub" in response.text
    assert "cursor=next" in response.text
    assert FakeKarakeepClient.calls == [False]


def test_archived_atom() -> None:
    FakeKarakeepClient.calls.clear()
    client = _test_client()

    response = client.get("/opds/archived.atom", headers=_auth_header())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/atom+xml")
    assert "Karakeep Archived" in response.text
    assert "/opds/archived.atom?cursor=next" in response.text
    assert FakeKarakeepClient.calls == [True]


def test_bookmarks_json() -> None:
    client = _test_client()

    response = client.get("/opds/bookmarks.json", headers=_auth_header())

    assert response.status_code == 200
    assert response.json()["publications"][0]["metadata"]["title"] == "Example"


def test_epub_endpoint() -> None:
    client = _test_client()
    response = client.get("/opds/bookmarks/abc.epub", headers=_auth_header())

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/epub+zip"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"
        article = archive.read("OEBPS/article.xhtml").decode()
    assert "Asset body" in article
    assert "Summary must not be used as body" not in article


def test_asset_proxy() -> None:
    client = _test_client()
    response = client.get("/opds/assets/cover", headers=_auth_header())

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"image"


def _test_client() -> TestClient:
    settings = Settings(
        karakeep_base_url="https://karakeep.example",
        karakeep_api_token="token",
        opds_username="user",
        opds_password="pass",
        opds_page_size=2,
        service_base_url="https://opds.example",
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_client] = lambda: FakeKarakeepClient()
    return TestClient(app)


def _auth_header(username: str = "user", password: str = "pass") -> dict[str, Any]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}
