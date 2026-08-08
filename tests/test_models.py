from __future__ import annotations

from karakeep_opds.models import Asset, Bookmark, BookmarkPage


def test_bookmark_from_api_uses_content_fields() -> None:
    bookmark = Bookmark.from_api(
        {
            "id": "abc",
            "createdAt": "2024-01-01T00:00:00Z",
            "modifiedAt": "2024-01-02T00:00:00Z",
            "tags": [{"name": "python"}],
            "assets": [{"id": "asset", "assetType": "image", "fileName": "cover.png"}],
            "content": {
                "title": "Title",
                "url": "https://example.test",
                "description": "Description",
                "htmlContent": "<p>Hello</p>",
                "imageAssetId": "image",
                "contentAssetId": "content",
            },
        }
    )

    assert bookmark.id == "abc"
    assert bookmark.title == "Title"
    assert bookmark.tags == ("python",)
    assert bookmark.assets[0].id == "asset"
    assert bookmark.cover_asset_id == "image"
    assert bookmark.content_asset_id == "content"
    assert bookmark.readable_content_asset_id == "content"


def test_bookmark_uses_link_html_content_asset_as_readable_fallback() -> None:
    bookmark = Bookmark(
        id="abc",
        title="Title",
        assets=(
            Asset(id="screen", asset_type="screenshot"),
            Asset(id="article", asset_type="linkHtmlContent"),
        ),
    )

    assert bookmark.readable_content_asset_id == "article"


def test_bookmark_page_from_api() -> None:
    page = BookmarkPage.from_api(
        {"bookmarks": [{"id": "abc", "title": "Title"}], "nextCursor": "n"}
    )

    assert len(page.bookmarks) == 1
    assert page.next_cursor == "n"
