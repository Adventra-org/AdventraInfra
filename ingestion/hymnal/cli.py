import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


class HymnalError(ValueError):
    pass


MANIFEST_FIELDS = {
    "revision", "source_uri", "approval_reference", "complete_catalog", "languages", "hymns",
}
HYMN_FIELDS = {
    "file", "rights", "hymn_id", "number", "language", "category", "copyright_notice",
    "composer", "key", "tempo", "time_signature", "tags", "mp3_url", "flac_url",
}
HEADING = re.compile(r"^\s*(\d{1,3})\s*[-\u2013\u2014]\s*(.+?)\s*$")
MAX_MANIFEST_BYTES = 100_000_000


def object_value(value: object, fields: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise HymnalError(f"{label} must be an object.")
    unexpected = set(value) - fields
    if unexpected:
        raise HymnalError(f"Unknown {label} fields: {', '.join(sorted(unexpected))}.")
    return dict(value)


def text(value: object, label: str, limit: int) -> str:
    if (
        not isinstance(value, str) or not value.strip()
        or len(value.encode("utf-8")) > limit or "\0" in value
    ):
        raise HymnalError(f"{label} must be nonempty, <= {limit} UTF-8 bytes, without NUL.")
    return value


def integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise HymnalError(f"{label} must be an integer in {minimum}-{maximum}.")
    return value


def language(value: object) -> str:
    tag = text(value, "language", 35)
    if not re.fullmatch(r"[a-z]{2,8}(?:-[a-z0-9]{1,8})*", tag):
        raise HymnalError("language must be a lowercase language tag.")
    return tag


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HymnalError(f"Duplicate JSON field: {key}.")
        result[key] = value
    return result


def read_bytes(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        contents = handle.read(limit + 1)
    if len(contents) > limit:
        raise HymnalError(f"Input file exceeds {limit} bytes: {path}.")
    return contents


def media_url(value: object, extension: str) -> str | None:
    if value is None:
        return None
    url = text(value, f"{extension}_url", 2048)
    try:
        parsed = urlsplit(url)
        port = parsed.port
        valid = (
            parsed.scheme == "https" and parsed.hostname
            and parsed.username is None and parsed.password is None
            and "?" not in url and "#" not in url and "\\" not in url
            and not any(character.isspace() for character in url)
            and parsed.path.lower().endswith(f".{extension}")
            and (port is None or 0 <= port <= 65535)
        )
    except ValueError as error:
        raise HymnalError(f"Invalid {extension} URL.") from error
    if not valid:
        raise HymnalError(f"{extension} requires a stable HTTPS URL without credentials/query/fragment.")
    return url


def record(raw_dir: Path, entry: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    rights = object_value(entry.get("rights"), {"status", "basis", "reference"}, "rights")
    basis = rights.get("basis")
    if rights.get("status") != "approved" or not isinstance(basis, str) or basis not in {
        "public_domain", "permission", "original",
    }:
        raise HymnalError("Each hymn needs approved rights with an explicit supported basis.")
    text(rights.get("reference"), "rights.reference", 4000)
    filename = text(entry.get("file"), "file", 2048)
    relative = Path(filename)
    if relative.is_absolute() or any(part in {".", ".."} for part in filename.split("/")):
        raise HymnalError("file must be a safe relative path inside raw-dir.")
    source = (raw_dir / relative).resolve()
    if not source.is_relative_to(raw_dir):
        raise HymnalError("Source file resolves outside raw-dir.")
    if not source.is_file():
        raise HymnalError(f"Source file is missing or not a regular file: {filename}.")
    contents = read_bytes(source, 101024)
    try:
        lines = contents.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    except UnicodeDecodeError as error:
        raise HymnalError(f"Source file is not UTF-8: {filename}.") from error
    heading_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if heading_index is None:
        raise HymnalError(f"Missing numbered heading in {filename}.")
    heading = HEADING.fullmatch(lines[heading_index])
    if heading is None or int(heading.group(1)) != entry["number"]:
        raise HymnalError(f"Invalid/mismatched numbered heading in {filename}.")
    lyrics = "\n".join(lines[heading_index + 1:]).strip("\n")
    hymn: dict[str, object] = {
        "hymn_id": entry["hymn_id"], "number": entry["number"], "language": entry["language"],
        "title": text(heading.group(2), "title", 500),
        "lyrics": text(lyrics, "lyrics", 100000),
        "category": text(entry.get("category"), "category", 120),
        "copyright_notice": text(entry.get("copyright_notice"), "copyright_notice", 4000),
    }
    for field in ["composer", "key", "time_signature"]:
        value = entry.get(field)
        hymn[field] = None if value is None else text(value, field, 500)
    tempo = entry.get("tempo")
    hymn["tempo"] = None if tempo is None else integer(tempo, "tempo", 1, 400)
    tags = entry.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 30:
        raise HymnalError("tags must be an array of at most 30 entries.")
    hymn["tags"] = [text(tag, "tag", 120) for tag in tags]
    hymn["mp3_url"] = media_url(entry.get("mp3_url"), "mp3")
    hymn["flac_url"] = media_url(entry.get("flac_url"), "flac")
    if hymn["flac_url"] is not None and hymn["mp3_url"] is None:
        raise HymnalError("FLAC mapping requires an MP3 mapping.")
    return hymn, {
        "file": filename, "sha256": hashlib.sha256(contents).hexdigest(),
        "hymn_id": entry["hymn_id"], "number": entry["number"], "language": entry["language"],
        "rights": rights,
    }


def convert(raw_dir: Path, metadata_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    raw_dir = raw_dir.resolve()
    if not raw_dir.is_dir():
        raise HymnalError("raw-dir must be an existing local directory.")
    metadata_bytes = read_bytes(metadata_path, MAX_MANIFEST_BYTES)
    metadata = object_value(
        json.loads(metadata_bytes, object_pairs_hook=unique_object), MANIFEST_FIELDS, "manifest",
    )
    revision = integer(metadata.get("revision"), "revision", 1, 2147483647)
    source_uri = text(metadata.get("source_uri"), "source_uri", 2048)
    approval = text(metadata.get("approval_reference"), "approval_reference", 2048)
    complete = metadata.get("complete_catalog")
    if not isinstance(complete, bool):
        raise HymnalError("complete_catalog must explicitly be true or false.")
    languages = metadata.get("languages")
    if not isinstance(languages, list) or not 1 <= len(languages) <= 100:
        raise HymnalError("languages must contain 1-100 unique language tags.")
    languages = [language(tag) for tag in languages]
    if len(set(languages)) != len(languages):
        raise HymnalError("Duplicate language tags.")
    entries = metadata.get("hymns")
    if not isinstance(entries, list) or not entries:
        raise HymnalError("hymns must be a nonempty array of reviewed metadata.")
    identities: set[tuple[str, str]] = set()
    numbers: dict[str, set[int]] = {tag: set() for tag in languages}
    id_numbers: dict[str, int] = {}
    number_ids: dict[int, str] = {}
    validated = []
    for value in entries:
        entry = object_value(value, HYMN_FIELDS, "hymn")
        number = integer(entry.get("number"), "number", 1, 696)
        identifier = text(entry.get("hymn_id"), "hymn_id", 80)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise HymnalError("hymn_id must contain only ASCII letters, digits, hyphens or underscores.")
        tag = language(entry.get("language"))
        if tag not in numbers:
            raise HymnalError("Hymn language is not declared in languages.")
        if (
            (identifier, tag) in identities or number in numbers[tag]
            or identifier in id_numbers and id_numbers[identifier] != number
            or number in number_ids and number_ids[number] != identifier
        ):
            raise HymnalError("Duplicate/inconsistent hymn identity, number or language.")
        identities.add((identifier, tag))
        numbers[tag].add(number)
        id_numbers[identifier] = number
        number_ids[number] = identifier
        validated.append(entry)
    missing = {tag: sorted(set(range(1, 697)) - present) for tag, present in numbers.items()}
    if any(not present for present in numbers.values()):
        raise HymnalError("Every declared language must have hymn records.")
    if complete and any(missing.values()):
        raise HymnalError("Complete catalog requires every number 1-696 in every language.")
    validated.sort(key=lambda entry: (
        integer(entry["number"], "number", 1, 696), language(entry["language"]),
    ))
    records = [record(raw_dir, entry) for entry in validated]
    manifest = {
        "revision": revision, "source_uri": source_uri, "approval_reference": approval,
        "complete_catalog": complete, "languages": languages,
        "hymns": [hymn for hymn, _ in records],
    }
    provenance = {
        "source_uri": source_uri, "approval_reference": approval, "revision": revision,
        "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        "records": len(records),
        "files": [source for _, source in records], "missing_numbers": missing,
        "normalization": "UTF-8; remove BOM and numbered title; CRLF/CR to LF; trim outer blank lyric lines.",
    }
    return manifest, provenance


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert reviewed local hymn text; never downloads or imports.")
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    temporary: Path | None = None
    try:
        output = args.output.resolve()
        if output.is_relative_to(args.raw_dir.resolve()) or output == args.metadata.resolve():
            raise HymnalError("Output must not overwrite metadata or files inside raw-dir.")
        manifest, provenance = convert(args.raw_dir, args.metadata)
        contents = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(contents) > MAX_MANIFEST_BYTES:
            raise HymnalError("Generated manifest exceeds the backend's 100 MB limit.")
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(output)
        provenance.update({
            "manifest_sha256": hashlib.sha256(contents).hexdigest(),
            "complete_catalog": manifest["complete_catalog"],
            "output": str(output),
        })
        print(json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False))
    except (HymnalError, OSError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(2, f"error: {error}\n")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    main()
