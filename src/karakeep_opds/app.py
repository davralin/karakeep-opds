from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, cast

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from karakeep_opds.auth import require_basic_auth
from karakeep_opds.client import KarakeepClient
from karakeep_opds.config import Settings, configure_logging, get_settings
from karakeep_opds.epub import build_epub
from karakeep_opds.models import Bookmark, BookmarkPage, Feed, FeedEntry, FeedLink
from karakeep_opds.render import now_iso, render_opds1, render_opds2

LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    app.state.karakeep_client = KarakeepClient(settings)
    try:
        yield
    finally:
        await app.state.karakeep_client.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Karakeep OPDS", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", dependencies=[Depends(require_basic_auth)])
    @app.get("/opds", dependencies=[Depends(require_basic_auth)])
    @app.get("/opds/", dependencies=[Depends(require_basic_auth)])
    async def opds_root(
        request: Request,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        return _atom_response(_navigation_feed(_external_base_url(request, settings)))

    @app.get("/opds.atom", dependencies=[Depends(require_basic_auth)])
    async def opds_root_atom(
        request: Request,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        return _atom_response(_navigation_feed(_external_base_url(request, settings)))

    @app.get("/opds.json", dependencies=[Depends(require_basic_auth)])
    async def opds_root_json(
        request: Request,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> JSONResponse:
        return JSONResponse(render_opds2(_navigation_feed(_external_base_url(request, settings))))

    @app.get("/opds/bookmarks.atom", dependencies=[Depends(require_basic_auth)])
    async def bookmarks_atom(
        request: Request,
        client: Annotated[KarakeepClient, Depends(get_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        cursor: Annotated[str, Query()] = "",
    ) -> Response:
        page = await _load_bookmarks(client, settings, cursor, archived=False)
        return _atom_response(
            _bookmarks_feed(
                page,
                _external_base_url(request, settings),
                cursor,
                path="bookmarks",
                title="Karakeep Bookmarks",
            )
        )

    @app.get("/opds/bookmarks.json", dependencies=[Depends(require_basic_auth)])
    async def bookmarks_json(
        request: Request,
        client: Annotated[KarakeepClient, Depends(get_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        cursor: Annotated[str, Query()] = "",
    ) -> JSONResponse:
        page = await _load_bookmarks(client, settings, cursor, archived=False)
        return JSONResponse(
            render_opds2(
                _bookmarks_feed(
                    page,
                    _external_base_url(request, settings),
                    cursor,
                    path="bookmarks",
                    title="Karakeep Bookmarks",
                )
            )
        )

    @app.get("/opds/archived.atom", dependencies=[Depends(require_basic_auth)])
    async def archived_atom(
        request: Request,
        client: Annotated[KarakeepClient, Depends(get_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        cursor: Annotated[str, Query()] = "",
    ) -> Response:
        page = await _load_bookmarks(client, settings, cursor, archived=True)
        return _atom_response(
            _bookmarks_feed(
                page,
                _external_base_url(request, settings),
                cursor,
                path="archived",
                title="Karakeep Archived",
            )
        )

    @app.get("/opds/archived.json", dependencies=[Depends(require_basic_auth)])
    async def archived_json(
        request: Request,
        client: Annotated[KarakeepClient, Depends(get_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        cursor: Annotated[str, Query()] = "",
    ) -> JSONResponse:
        page = await _load_bookmarks(client, settings, cursor, archived=True)
        return JSONResponse(
            render_opds2(
                _bookmarks_feed(
                    page,
                    _external_base_url(request, settings),
                    cursor,
                    path="archived",
                    title="Karakeep Archived",
                )
            )
        )

    @app.get("/opds/bookmarks/{bookmark_id}.epub", dependencies=[Depends(require_basic_auth)])
    async def bookmark_epub(
        bookmark_id: str,
        client: Annotated[KarakeepClient, Depends(get_client)],
    ) -> Response:
        bookmark = await _load_bookmark(client, bookmark_id)
        asset_content = ""
        if bookmark.readable_content_asset_id:
            try:
                content, media_type = await client.get_asset(bookmark.readable_content_asset_id)
                if media_type.split(";", 1)[0] in {"text/html", "application/xhtml+xml"}:
                    asset_content = content.decode("utf-8", errors="replace")
            except httpx.HTTPError:
                LOGGER.warning("Failed to load content asset for bookmark %s", bookmark_id)
        epub = build_epub(bookmark, asset_content)
        filename = _safe_filename(bookmark.title) + ".epub"
        return Response(
            content=epub,
            media_type="application/epub+zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/opds/assets/{asset_id}", dependencies=[Depends(require_basic_auth)])
    async def asset_proxy(
        asset_id: str,
        client: Annotated[KarakeepClient, Depends(get_client)],
    ) -> Response:
        try:
            content, media_type = await client.get_asset(asset_id)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code, detail="Asset not found"
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Karakeep asset request failed") from exc
        return Response(content=content, media_type=media_type)

    return app


def get_client(request: Request) -> KarakeepClient:
    return cast(KarakeepClient, request.app.state.karakeep_client)


async def _load_bookmarks(
    client: KarakeepClient,
    settings: Settings,
    cursor: str,
    archived: bool,
) -> BookmarkPage:
    try:
        return await client.list_bookmarks(
            limit=settings.opds_page_size,
            cursor=cursor,
            archived=archived,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Karakeep bookmarks request failed") from exc


async def _load_bookmark(client: KarakeepClient, bookmark_id: str) -> Bookmark:
    try:
        return await client.get_bookmark(bookmark_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code, detail="Bookmark not found"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Karakeep bookmark request failed") from exc


def _navigation_feed(base_url: str) -> Feed:
    return Feed(
        id=f"{base_url}/opds",
        title="Karakeep",
        updated=now_iso(),
        links=(
            FeedLink(href=f"{base_url}/opds.atom", rel="self", title="OPDS 1.2"),
            FeedLink(
                href=f"{base_url}/opds.json",
                rel="alternate",
                media_type="application/opds+json",
                title="OPDS 2",
            ),
        ),
        entries=(
            FeedEntry(
                id=f"{base_url}/opds/bookmarks.atom",
                title="Bookmarks/",
                updated=now_iso(),
                summary="Unread Karakeep bookmarks as on-demand EPUB publications.",
                links=(
                    FeedLink(href=f"{base_url}/opds/bookmarks.atom", title="OPDS 1.2"),
                    FeedLink(
                        href=f"{base_url}/opds/bookmarks.json",
                        rel="alternate",
                        media_type="application/opds+json",
                        title="OPDS 2",
                    ),
                ),
            ),
            FeedEntry(
                id=f"{base_url}/opds/archived.atom",
                title="Archived/",
                updated=now_iso(),
                summary="Archived Karakeep bookmarks as on-demand EPUB publications.",
                links=(
                    FeedLink(href=f"{base_url}/opds/archived.atom", title="OPDS 1.2"),
                    FeedLink(
                        href=f"{base_url}/opds/archived.json",
                        rel="alternate",
                        media_type="application/opds+json",
                        title="OPDS 2",
                    ),
                ),
            ),
        ),
    )


def _bookmarks_feed(
    page: BookmarkPage,
    base_url: str,
    cursor: str,
    *,
    path: str,
    title: str,
) -> Feed:
    links = [
        FeedLink(href=f"{base_url}/opds/{path}.atom", rel="start", title="First page"),
        FeedLink(href=f"{base_url}/opds/{path}.atom", rel="self", title="Current page"),
        FeedLink(
            href=f"{base_url}/opds/{path}.json",
            rel="alternate",
            media_type="application/opds+json",
            title="OPDS 2",
        ),
    ]
    if page.next_cursor:
        links.append(
            FeedLink(
                href=f"{base_url}/opds/{path}.atom?cursor={page.next_cursor}",
                rel="next",
                title="Next page",
            )
        )
    if cursor:
        title = f"{title} (continued)"
    return Feed(
        id=f"{base_url}/opds/{path}.atom?cursor={cursor}"
        if cursor
        else f"{base_url}/opds/{path}.atom",
        title=title,
        updated=now_iso(),
        links=tuple(links),
        entries=tuple(_bookmark_entry(bookmark, base_url) for bookmark in page.bookmarks),
    )


def _bookmark_entry(bookmark: Bookmark, base_url: str) -> FeedEntry:
    links = [
        FeedLink(
            href=f"{base_url}/opds/bookmarks/{bookmark.id}.epub",
            rel="http://opds-spec.org/acquisition",
            media_type="application/epub+zip",
            title="EPUB",
        )
    ]
    if bookmark.url:
        links.append(
            FeedLink(href=bookmark.url, rel="alternate", media_type="text/html", title="Source")
        )
    if bookmark.cover_asset_id:
        links.append(
            FeedLink(
                href=f"{base_url}/opds/assets/{bookmark.cover_asset_id}",
                rel="http://opds-spec.org/image",
                media_type="image/*",
                title="Cover",
            )
        )
    authors = tuple(value for value in [bookmark.author, bookmark.publisher] if value)
    return FeedEntry(
        id=f"karakeep:{bookmark.id}",
        title=bookmark.title,
        updated=bookmark.updated_at or now_iso(),
        summary=bookmark.description,
        authors=authors,
        tags=bookmark.tags,
        links=tuple(links),
    )


def _external_base_url(request: Request, settings: Settings) -> str:
    if settings.service_base_url:
        return settings.service_base_url
    return str(request.base_url).rstrip("/")


def _atom_response(feed: Feed) -> Response:
    return Response(
        content=render_opds1(feed),
        media_type="application/atom+xml;profile=opds-catalog;kind=acquisition",
    )


def _safe_filename(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value).strip(
        "-"
    )
    return safe[:80] or "bookmark"


app = create_app()


def main() -> None:
    uvicorn.run("karakeep_opds.app:app", host="0.0.0.0", port=8000, factory=False)
