from __future__ import annotations

import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from io import BytesIO
from xml.sax.saxutils import escape

from karakeep_opds.models import Bookmark

_VOID_TAGS = {"br", "hr"}
_BLOCK_TAGS = {
    "blockquote",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "ol",
    "p",
    "pre",
    "table",
    "ul",
}
_ALLOWED_TAGS = (
    _VOID_TAGS
    | _BLOCK_TAGS
    | {
        "a",
        "b",
        "caption",
        "code",
        "em",
        "li",
        "strong",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
    }
)
_SUPPRESSED_TAGS = {"embed", "form", "iframe", "object", "script", "style", "svg", "video", "audio"}


@dataclass
class _Node:
    tag: str
    attributes: dict[str, str] = field(default_factory=dict)
    children: list[_Node | str] = field(default_factory=list)


@dataclass
class _OpenElement:
    tag: str
    node: _Node | None
    suppressed: bool = False


class _TextFirstParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self._open = [_OpenElement("root", self.root)]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._is_suppressed() or tag in _SUPPRESSED_TAGS:
            self._open.append(_OpenElement(tag, None, suppressed=True))
            return
        if tag == "img":
            alt = next((value for name, value in attrs if name.lower() == "alt" and value), "")
            if alt:
                self._current_node().children.append(f"[Image: {alt}]")
            return
        if tag not in _ALLOWED_TAGS:
            self._open.append(_OpenElement(tag, None))
            return
        self._close_for_start(tag)
        attributes: dict[str, str] = {}
        if tag == "a":
            href = next((value for name, value in attrs if name.lower() == "href" and value), "")
            if href.startswith(("https://", "http://", "mailto:")):
                attributes["href"] = href
            else:
                self._open.append(_OpenElement(tag, None))
                return
        node = _Node(tag, attributes)
        self._current_node().children.append(node)
        if tag not in _VOID_TAGS:
            self._open.append(_OpenElement(tag, node))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].tag == tag:
                del self._open[index:]
                return

    def handle_data(self, data: str) -> None:
        if data and not self._is_suppressed():
            self._current_node().children.append(data)

    def _current_node(self) -> _Node:
        for element in reversed(self._open):
            if element.node is not None:
                return element.node
        return self.root

    def _is_suppressed(self) -> bool:
        return any(element.suppressed for element in self._open)

    def _close_for_start(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._close_open("p")
        if tag == "li":
            self._close_open("li")
        if tag == "tr":
            self._close_open("tr")
        if tag in {"td", "th"}:
            self._close_open("td")
            self._close_open("th")

    def _close_open(self, tag: str) -> None:
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].tag == tag:
                del self._open[index:]
                return


def build_epub(bookmark: Bookmark, asset_content: str = "") -> bytes:
    book_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"karakeep:{bookmark.id}"))
    title = bookmark.title or "Untitled"
    body = _body_xhtml(bookmark, asset_content)

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        archive.writestr("META-INF/container.xml", _container_xml())
        archive.writestr("OEBPS/content.opf", _content_opf(book_uuid, title, bookmark))
        archive.writestr("OEBPS/style.css", _stylesheet())
        archive.writestr("OEBPS/nav.xhtml", _nav_xhtml(title))
        archive.writestr("OEBPS/article.xhtml", _article_xhtml(title, body))
    return buffer.getvalue()


def _body_xhtml(bookmark: Bookmark, asset_content: str) -> str:
    content = asset_content or bookmark.html_content
    if content:
        parser = _TextFirstParser()
        parser.feed(content)
        parser.close()
        rendered = _render_children(parser.root)
        if rendered.strip():
            return rendered
    parts = ["<p>No readable article content was available for this bookmark.</p>"]
    if bookmark.url:
        href = escape(bookmark.url, {'"': "&quot;"})
        parts.append(f'<p>Source: <a href="{href}">{escape(bookmark.url)}</a></p>')
    return "\n".join(parts)


def _render_children(node: _Node) -> str:
    return "".join(_render_child(child) for child in node.children)


def _render_child(child: _Node | str) -> str:
    if isinstance(child, str):
        return escape(child)
    attributes = "".join(
        f' {name}="{escape(value, {'"': "&quot;"})}"' for name, value in child.attributes.items()
    )
    if child.tag in _VOID_TAGS:
        return f"<{child.tag}{attributes} />"
    return f"<{child.tag}{attributes}>{_render_children(child)}</{child.tag}>"


def _container_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
"""


def _content_opf(book_uuid: str, title: str, bookmark: Bookmark) -> str:
    author = bookmark.author or bookmark.publisher or "Karakeep"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:{escape(book_uuid)}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:creator>{escape(author)}</dc:creator>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">{_modified_timestamp(bookmark.updated_at)}</meta>
  </metadata>
  <manifest>
    <item id="style" href="style.css" media-type="text/css" />
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="article" href="article.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="article" />
  </spine>
</package>
"""


def _modified_timestamp(value: str) -> str:
    try:
        timestamp = (
            datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.now(UTC)
        )
    except ValueError:
        timestamp = datetime.now(UTC)
    return timestamp.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stylesheet() -> str:
    return """body { font-family: serif; line-height: 1.45; margin: 5%; }
pre, code { font-family: monospace; white-space: pre-wrap; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #777; padding: 0.35em; vertical-align: top; }
"""


def _nav_xhtml(title: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head>
    <title>{escape(title)}</title>
    <link rel="stylesheet" type="text/css" href="style.css" />
  </head>
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
    <link rel="stylesheet" type="text/css" href="style.css" />
  </head>
  <body>
    <h1>{escape(title)}</h1>
    {body}
  </body>
</html>
"""
