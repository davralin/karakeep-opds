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
        assert archive.infolist()[0].filename == "mimetype"
        assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
        assert "META-INF/container.xml" in archive.namelist()
        assert "OEBPS/content.opf" in archive.namelist()
        assert "OEBPS/style.css" in archive.namelist()
        assert "OEBPS/article.xhtml" in archive.namelist()
        for name in [
            "META-INF/container.xml",
            "OEBPS/content.opf",
            "OEBPS/nav.xhtml",
            "OEBPS/article.xhtml",
        ]:
            ElementTree.fromstring(archive.read(name))


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


def test_build_epub_converts_malformed_html_to_safe_text_first_xhtml() -> None:
    content = build_epub(
        Bookmark(
            id="abc",
            title="Fish & Chips",
            html_content="""
                <h2>Heading & unescaped ampersand</h2>
                <p onclick="steal()">A <strong>readable</strong> paragraph<br>with a line break.
                <script>alert('discarded')</script><iframe src="https://tracker.test"></iframe>
                <a href="https://example.test/article">External link</a>
                <a href="/relative">Relative link</a>
                <img src="https://example.test/image.png" alt="Useful image description">
                <ul><li>One<li>Two</ul>
                <table><tr><th>Name<th>Value<tr><td>Tea<td>2</table>
            """,
        )
    )

    with zipfile.ZipFile(BytesIO(content)) as archive:
        article = archive.read("OEBPS/article.xhtml").decode()
        ElementTree.fromstring(article)

    assert "Heading &amp; unescaped ampersand" in article
    assert "<strong>readable</strong>" in article
    assert "<br />" in article
    assert "alert" not in article
    assert "iframe" not in article
    assert "onclick" not in article
    assert "<img" not in article
    assert "[Image: Useful image description]" in article
    assert '<a href="https://example.test/article">External link</a>' in article
    assert "Relative link" in article
    assert 'href="/relative"' not in article
    assert "<li>One</li><li>Two</li>" in article
    assert "<th>Name</th><th>Value</th>" in article


def test_build_epub_normalizes_modified_timestamp() -> None:
    content = build_epub(
        Bookmark(
            id="abc",
            title="Example",
            html_content="<p>Body</p>",
            modified_at="2024-01-01T01:02:03.123+01:00",
        )
    )

    with zipfile.ZipFile(BytesIO(content)) as archive:
        package = archive.read("OEBPS/content.opf").decode()

    assert "2024-01-01T00:02:03Z" in package
