from __future__ import annotations

import html
import re
import uuid
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from xml.sax.saxutils import escape

from karakeep_opds.models import Bookmark


def build_epub(bookmark: Bookmark, asset_content: str = "") -> bytes:
    book_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"karakeep:{bookmark.id}"))
    title = bookmark.title or "Untitled"
    body = _body_html(bookmark, asset_content)

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        archive.writestr("META-INF/container.xml", _container_xml())
        archive.writestr("OEBPS/content.opf", _content_opf(book_uuid, title, bookmark))
        archive.writestr("OEBPS/nav.xhtml", _nav_xhtml(title))
        archive.writestr("OEBPS/article.xhtml", _article_xhtml(title, body))
    return buffer.getvalue()


def _body_html(bookmark: Bookmark, asset_content: str) -> str:
    if asset_content:
        return _xhtml_fragment(asset_content)
    if bookmark.html_content:
        return _xhtml_fragment(bookmark.html_content)
    parts = []
    parts.append("<p>No readable article content was available for this bookmark.</p>")
    if bookmark.url:
        href = escape(bookmark.url, {'"': "&quot;"})
        parts.append(f'<p>Source: <a href="{href}">{escape(bookmark.url)}</a></p>')
    return "\n".join(parts)


def _xhtml_fragment(value: str) -> str:
    fragment = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", "", value, flags=re.I)
    fragment = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", "", fragment, flags=re.I)
    if "<body" in fragment.lower():
        match = re.search(r"<body[^>]*>(.*)</body>", fragment, flags=re.I | re.S)
        if match:
            fragment = match.group(1)
    # Karakeep readability assets are HTML fragments.
    return fragment


def _container_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
"""


def _content_opf(book_uuid: str, title: str, bookmark: Bookmark) -> str:
    updated = bookmark.updated_at or datetime.now(UTC).isoformat()
    author = bookmark.author or bookmark.publisher or "Karakeep"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:{escape(book_uuid)}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:creator>{escape(author)}</dc:creator>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">{escape(updated)}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="article" href="article.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="article" />
  </spine>
</package>
"""


def _nav_xhtml(title: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>{escape(title)}</title></head>
  <body>
    <nav epub:type="toc"><ol><li><a href="article.xhtml">{escape(title)}</a></li></ol></nav>
  </body>
</html>
"""


def _article_xhtml(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>{escape(title)}</title>
    <meta charset="utf-8" />
  </head>
  <body>
    <h1>{escape(title)}</h1>
    {html.unescape(body)}
  </body>
</html>
"""
