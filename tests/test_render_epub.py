from __future__ import annotations

import json
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from karakeep_opds.epub import build_epub
from karakeep_opds.models import Bookmark, Feed, FeedEntry, FeedLink
from karakeep_opds.render import render_opds1, render_opds2


def test_render_opds1_is_xml() -> None:
    xml = render_opds1(
        Feed(
            id="catalog",
            title="Catalog",
            updated="2024-01-01T00:00:00Z",
            links=(FeedLink(href="https://example.test/opds", rel="self"),),
            entries=(FeedEntry(id="entry", title="Entry", updated="2024-01-01T00:00:00Z"),),
        )
    )

    root = ElementTree.fromstring(xml)

    assert root.tag.endswith("feed")
    assert root.find("{http://www.w3.org/2005/Atom}entry") is not None


def test_render_opds2_is_json_serializable() -> None:
    data = render_opds2(
        Feed(
            id="catalog",
            title="Catalog",
            updated="2024-01-01T00:00:00Z",
            entries=(FeedEntry(id="entry", title="Entry", updated="2024-01-01T00:00:00Z"),),
        )
    )

    assert data["metadata"] == {"title": "Catalog", "modified": "2024-01-01T00:00:00Z"}
    json.dumps(data)


def test_build_epub_creates_required_files() -> None:
    content = build_epub(
        Bookmark(
            id="abc",
            title="Example",
            description="Description",
            html_content="<p>Hello</p>",
            modified_at="2024-01-01T00:00:00Z",
        )
    )

    with zipfile.ZipFile(BytesIO(content)) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"
        assert "META-INF/container.xml" in archive.namelist()
        assert "OEBPS/content.opf" in archive.namelist()
        assert "OEBPS/article.xhtml" in archive.namelist()


def test_build_epub_does_not_use_summary_or_description_as_body() -> None:
    content = build_epub(
        Bookmark(
            id="abc",
            title="Example",
            url="https://example.test/article",
            description="Summary text should not become book content",
            modified_at="2024-01-01T00:00:00Z",
        )
    )

    with zipfile.ZipFile(BytesIO(content)) as archive:
        article = archive.read("OEBPS/article.xhtml").decode()

    assert "Summary text should not become book content" not in article
    assert "No readable article content was available" in article
    assert "https://example.test/article" in article
