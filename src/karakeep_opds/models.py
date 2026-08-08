from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Asset:
    id: str
    asset_type: str = ""
    file_name: str = ""


@dataclass(frozen=True)
class Bookmark:
    id: str
    title: str
    url: str = ""
    description: str = ""
    html_content: str = ""
    created_at: str = ""
    modified_at: str = ""
    author: str = ""
    publisher: str = ""
    content_type: str = ""
    content_asset_id: str = ""
    image_asset_id: str = ""
    screenshot_asset_id: str = ""
    tags: tuple[str, ...] = ()
    assets: tuple[Asset, ...] = ()

    @property
    def updated_at(self) -> str:
        return self.modified_at or self.created_at

    @property
    def cover_asset_id(self) -> str:
        return self.image_asset_id or self.screenshot_asset_id

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Bookmark:
        content = _dict(data.get("content"))
        title = _str(data.get("title")) or _str(content.get("title")) or "Untitled"
        description = (
            _str(content.get("description")) or _str(data.get("summary")) or _str(data.get("note"))
        )
        tags = tuple(
            _str(tag.get("name"))
            for tag in _list_of_dicts(data.get("tags"))
            if _str(tag.get("name"))
        )
        assets = tuple(
            Asset(
                id=_str(asset.get("id")),
                asset_type=_str(asset.get("assetType")),
                file_name=_str(asset.get("fileName")),
            )
            for asset in _list_of_dicts(data.get("assets"))
            if _str(asset.get("id"))
        )
        return cls(
            id=_str(data.get("id")),
            title=title,
            url=_str(content.get("url")),
            description=description,
            html_content=_str(content.get("htmlContent")),
            created_at=_str(data.get("createdAt")),
            modified_at=_str(data.get("modifiedAt")),
            author=_str(content.get("author")),
            publisher=_str(content.get("publisher")),
            content_type=_str(content.get("type")),
            content_asset_id=_str(content.get("contentAssetId")),
            image_asset_id=_str(content.get("imageAssetId")),
            screenshot_asset_id=_str(content.get("screenshotAssetId")),
            tags=tags,
            assets=assets,
        )


@dataclass(frozen=True)
class BookmarkPage:
    bookmarks: tuple[Bookmark, ...]
    next_cursor: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> BookmarkPage:
        return cls(
            bookmarks=tuple(
                Bookmark.from_api(item) for item in _list_of_dicts(data.get("bookmarks"))
            ),
            next_cursor=_str(data.get("nextCursor")),
        )


@dataclass(frozen=True)
class FeedLink:
    href: str
    rel: str = "subsection"
    media_type: str = "application/atom+xml;profile=opds-catalog;kind=acquisition"
    title: str = ""


@dataclass(frozen=True)
class FeedEntry:
    id: str
    title: str
    updated: str
    summary: str = ""
    authors: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    links: tuple[FeedLink, ...] = ()


@dataclass(frozen=True)
class Feed:
    id: str
    title: str
    updated: str
    links: tuple[FeedLink, ...] = ()
    entries: tuple[FeedEntry, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""
