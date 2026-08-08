from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree

from karakeep_opds.models import Feed, FeedEntry, FeedLink

ATOM_NS = "http://www.w3.org/2005/Atom"
DC_NS = "http://purl.org/dc/terms/"
OPDS_NS = "http://opds-spec.org/2010/catalog"

ElementTree.register_namespace("", ATOM_NS)
ElementTree.register_namespace("dcterms", DC_NS)
ElementTree.register_namespace("opds", OPDS_NS)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def render_opds1(feed: Feed) -> str:
    root = ElementTree.Element(_tag(ATOM_NS, "feed"), {"xmlns:opds": OPDS_NS})
    _text(root, "id", feed.id)
    _text(root, "title", feed.title)
    _text(root, "updated", feed.updated or now_iso())
    for link in feed.links:
        root.append(_link(link))
    for entry in feed.entries:
        root.append(_entry(entry))
    return ElementTree.tostring(root, encoding="unicode", xml_declaration=True)


def render_opds2(feed: Feed) -> dict[str, object]:
    return {
        "metadata": {
            "title": feed.title,
            "modified": feed.updated or now_iso(),
            **feed.metadata,
        },
        "links": [_opds2_link(link) for link in feed.links],
        "publications": [_opds2_entry(entry) for entry in feed.entries],
    }


def _entry(entry: FeedEntry) -> ElementTree.Element:
    element = ElementTree.Element(_tag(ATOM_NS, "entry"))
    _text(element, "id", entry.id)
    _text(element, "title", entry.title)
    _text(element, "updated", entry.updated or now_iso())
    if entry.summary:
        _text(element, "summary", entry.summary)
    for author in entry.authors:
        author_el = ElementTree.SubElement(element, _tag(ATOM_NS, "author"))
        _text(author_el, "name", author)
    for tag in entry.tags:
        ElementTree.SubElement(element, _tag(ATOM_NS, "category"), {"term": tag})
    for link in entry.links:
        element.append(_link(link))
    return element


def _link(link: FeedLink) -> ElementTree.Element:
    attributes = {"href": link.href, "rel": link.rel, "type": link.media_type}
    if link.title:
        attributes["title"] = link.title
    return ElementTree.Element(_tag(ATOM_NS, "link"), attributes)


def _text(parent: ElementTree.Element, name: str, text: str) -> None:
    child = ElementTree.SubElement(parent, _tag(ATOM_NS, name))
    child.text = text


def _tag(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _opds2_link(link: FeedLink) -> dict[str, str]:
    data = {"href": link.href, "rel": link.rel, "type": link.media_type}
    if link.title:
        data["title"] = link.title
    return data


def _opds2_entry(entry: FeedEntry) -> dict[str, object]:
    metadata: dict[str, object] = {
        "title": entry.title,
        "modified": entry.updated or now_iso(),
    }
    if entry.authors:
        metadata["author"] = [{"name": author} for author in entry.authors]
    if entry.summary:
        metadata["description"] = entry.summary
    if entry.tags:
        metadata["subject"] = list(entry.tags)
    return {
        "metadata": metadata,
        "links": [_opds2_link(link) for link in entry.links],
    }
