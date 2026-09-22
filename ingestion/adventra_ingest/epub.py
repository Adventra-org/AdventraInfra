from __future__ import annotations

import hashlib
import re
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from .models import BookDocument, Chapter, Passage

_CONTAINER_PATH = "META-INF/container.xml"
_CONTAINER_NS = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}
_OPF_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "opf": "http://www.idpf.org/2007/opf",
}
_IGNORED_TAGS = {"script", "style", "nav"}
_HEADING_TAGS = {"h1", "h2", "h3"}
_PASSAGE_TAGS = {"p", "blockquote", "li"}
_SPACE = re.compile(r"\s+")
_CHAPTER_NUMBER = re.compile(r"\bchapter\s+(\d+)\b", re.IGNORECASE)
_NON_CONTENT_IDS = {"cover", "titlepage", "toc", "nav", "aboutbook"}


class EpubError(ValueError):
    pass


def parse_epub(path: Path, source_root: Path | None = None) -> BookDocument:
    source = path.resolve()
    if not source.is_file():
        raise EpubError(f"EPUB does not exist: {path}")

    source_bytes = source.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    try:
        with zipfile.ZipFile(source) as archive:
            opf_path = _find_opf_path(archive)
            package = ElementTree.fromstring(archive.read(str(opf_path)))
            metadata = _metadata(package)
            documents = _spine_documents(package, opf_path)
            chapters = _parse_chapters(
                archive,
                documents,
                _slug(metadata["title"]),
                metadata["language"],
            )
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as error:
        raise EpubError(f"Invalid EPUB {path}: {error}") from error

    if not chapters:
        raise EpubError(f"EPUB contains no readable chapters: {path}")

    relative_source = (
        source.relative_to(source_root.resolve()).as_posix()
        if source_root is not None and source.is_relative_to(source_root.resolve())
        else source.name
    )
    return BookDocument(
        book_id=_slug(metadata["title"]),
        title=metadata["title"],
        author=metadata["author"],
        language=metadata["language"],
        source_identifier=metadata.get("identifier"),
        publisher=metadata.get("publisher"),
        source_path=relative_source,
        source_sha256=source_sha256,
        content_version=source_sha256[:16],
        chapters=tuple(chapters),
    )


def _find_opf_path(archive: zipfile.ZipFile) -> PurePosixPath:
    container = ElementTree.fromstring(archive.read(_CONTAINER_PATH))
    rootfile = container.find(".//container:rootfile", _CONTAINER_NS)
    if rootfile is None or not rootfile.attrib.get("full-path"):
        raise EpubError("EPUB container does not identify a package document")
    return PurePosixPath(rootfile.attrib["full-path"])


def _metadata(package: ElementTree.Element) -> dict[str, str]:
    def required(name: str, fallback: str | None = None) -> str:
        element = package.find(f".//dc:{name}", _OPF_NS)
        value = _clean_text(element.text if element is not None else "")
        if value:
            return value
        if fallback is not None:
            return fallback
        raise EpubError(f"EPUB metadata is missing dc:{name}")

    result = {
        "title": required("title"),
        "author": required("creator", "Unknown"),
        "language": required("language", "en").lower(),
    }
    for name in ("identifier", "publisher"):
        element = package.find(f".//dc:{name}", _OPF_NS)
        value = _clean_text(element.text if element is not None else "")
        if value:
            result[name] = value
    return result


def _spine_documents(
    package: ElementTree.Element, opf_path: PurePosixPath
) -> list[PurePosixPath]:
    manifest = {
        item.attrib["id"]: (
            item.attrib["href"],
            set(item.attrib.get("properties", "").split()),
        )
        for item in package.findall(".//opf:manifest/opf:item", _OPF_NS)
        if item.attrib.get("id") and item.attrib.get("href")
    }
    result: list[PurePosixPath] = []
    for itemref in package.findall(".//opf:spine/opf:itemref", _OPF_NS):
        item_id = itemref.attrib.get("idref", "")
        item = manifest.get(item_id)
        if item:
            href, properties = item
            if (
                item_id.lower() in _NON_CONTENT_IDS
                or "nav" in properties
                or PurePosixPath(href).stem.lower() in _NON_CONTENT_IDS
            ):
                continue
            result.append(opf_path.parent / PurePosixPath(href))
    return result


def _parse_chapters(
    archive: zipfile.ZipFile,
    documents: list[PurePosixPath],
    book_id: str,
    language: str,
) -> list[Chapter]:
    chapters: list[Chapter] = []
    for document in documents:
        root = ElementTree.fromstring(archive.read(str(document)))
        title = _document_title(root)
        paragraphs = [
            text
            for element in root.iter()
            if _local_name(element.tag) in _PASSAGE_TAGS
            and (text := _element_text(element))
        ]
        if not title or not paragraphs:
            continue

        number_match = _CHAPTER_NUMBER.search(title)
        number = int(number_match.group(1)) if number_match else len(chapters)
        if any(chapter.number == number for chapter in chapters):
            number = max((chapter.number for chapter in chapters), default=-1) + 1
        chapter_id = f"{book_id}:{language}:chapter-{number:03d}"
        passages = tuple(
            Passage(
                passage_id=f"{chapter_id}:p{index:04d}",
                chapter_id=chapter_id,
                chapter_number=number,
                paragraph_number=index,
                sequence=index,
                text=text,
                content_hash=_sha256(text),
            )
            for index, text in enumerate(paragraphs, start=1)
        )
        chapters.append(
            Chapter(
                chapter_id=chapter_id,
                number=number,
                title=title,
                passages=passages,
            )
        )
    return chapters


def _document_title(root: ElementTree.Element) -> str:
    for element in root.iter():
        if _local_name(element.tag) in _HEADING_TAGS:
            title = _element_text(element)
            if title:
                return title
    return ""


def _element_text(element: ElementTree.Element) -> str:
    parts: list[str] = []

    def visit(node: ElementTree.Element) -> None:
        tag = _local_name(node.tag)
        classes = set(node.attrib.get("class", "").lower().split())
        if tag in _IGNORED_TAGS or "pagebreak" in classes:
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(element)
    return _clean_text(" ".join(parts))


def _clean_text(value: str | None) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFC", value or "")).strip()


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
