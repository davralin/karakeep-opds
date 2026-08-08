from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from karakeep_opds.config import Settings
from karakeep_opds.models import Bookmark, BookmarkPage


class KarakeepClient:
    def __init__(self, settings: Settings) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.karakeep_api_base_url,
            headers={
                "Authorization": f"Bearer {settings.karakeep_api_token}",
                "Accept": "application/json",
                "User-Agent": "karakeep-opds/0.1.0",
            },
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def list_bookmarks(self, *, limit: int, cursor: str = "") -> BookmarkPage:
        params: dict[str, str | int] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = await self._client.get("/bookmarks", params=params)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected bookmarks response")
        return BookmarkPage.from_api(data)

    async def get_bookmark(self, bookmark_id: str) -> Bookmark:
        response = await self._client.get(f"/bookmarks/{bookmark_id}")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected bookmark response")
        return Bookmark.from_api(data)

    async def get_asset(self, asset_id: str) -> tuple[bytes, str]:
        response = await self._client.get(f"/assets/{asset_id}", headers={"Accept": "*/*"})
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "application/octet-stream")


async def client_context(settings: Settings) -> AsyncIterator[KarakeepClient]:
    client = KarakeepClient(settings)
    try:
        yield client
    finally:
        await client.close()
