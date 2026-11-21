import argparse
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .cli import HymnalError, MAX_MANIFEST_BYTES, integer, read_bytes, unique_object


def verify(manifest: dict, api_url: str) -> int:
    if (
        not isinstance(manifest, dict) or not isinstance(manifest.get("languages"), list)
        or not manifest["languages"] or not isinstance(manifest.get("hymns"), list)
        or not manifest["hymns"]
    ):
        raise HymnalError("Expected a validated, nonempty hymnal import manifest.")
    integer(manifest.get("revision"), "revision", 1, 2147483647)
    languages = manifest["languages"]
    if any(not isinstance(tag, str) for tag in languages) or len(set(languages)) != len(languages):
        raise HymnalError("Manifest languages must be unique strings.")
    identities = set()
    for hymn in manifest["hymns"]:
        if (
            not isinstance(hymn, dict) or not isinstance(hymn.get("hymn_id"), str)
            or not hymn["hymn_id"] or hymn.get("language") not in languages
        ):
            raise HymnalError("Invalid manifest hymn identity or language.")
        identity = (hymn["hymn_id"], hymn["language"])
        if identity in identities:
            raise HymnalError("Duplicate manifest hymn identity.")
        identities.add(identity)
    url = urlsplit(api_url)
    if (
        url.scheme != "https" or not url.hostname or url.username or url.password
        or url.query or url.fragment or url.path not in {"", "/"}
    ):
        raise HymnalError("API URL must be an HTTPS origin without credentials, path, query or fragment.")
    fields = (
        "hymn_id", "number", "language", "title", "lyrics", "category",
        "copyright_notice", "composer", "key", "tempo", "time_signature",
    )
    matched = 0
    for language in manifest["languages"]:
        expected = {h["hymn_id"]: h for h in manifest["hymns"] if h["language"] == language}
        seen = set()
        for page in range(1, 8):
            query = urlencode({"language": language, "page": page, "page_size": 100})
            request = urllib.request.Request(
                f"{api_url.rstrip('/')}/v1/hymnal/hymns?{query}",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
            if not isinstance(data, list) or len(data) > 100:
                raise HymnalError("Hymnal API returned an invalid page.")
            for row in data:
                if (
                    not isinstance(row, dict) or not isinstance(row.get("hymn_id"), str)
                    or row.get("language") != language or row["hymn_id"] in seen
                ):
                    raise HymnalError("Hymnal API returned invalid/duplicate identities or language.")
                seen.add(row["hymn_id"])
                hymn = expected.get(row["hymn_id"])
                if hymn is not None:
                    for field in fields:
                        if type(row.get(field)) is not type(hymn.get(field)) or row.get(field) != hymn.get(field):
                            raise HymnalError(f"API mismatch for {hymn['hymn_id']}/{language}: {field}.")
                    if (
                        type(row.get("revision")) is not int or row.get("revision") != manifest["revision"]
                        or row.get("tags") != hymn.get("tags", [])
                        or row.get("has_accompaniment") is not (hymn.get("mp3_url") is not None)
                    ):
                        raise HymnalError(f"API metadata mismatch for {hymn['hymn_id']}/{language}.")
                    matched += 1
            if len(data) < 100:
                break
        missing = set(expected) - seen
        if missing:
            raise HymnalError(f"API is missing {len(missing)} imported hymns in {language}.")
    if matched != len(manifest["hymns"]):
        raise HymnalError("API verified-record count differs from the imported manifest.")
    return matched


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify published hymns without logging lyric text.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--api-url", required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(
            read_bytes(args.manifest, MAX_MANIFEST_BYTES), object_pairs_hook=unique_object
        )
        records = verify(manifest, args.api_url)
        print(json.dumps({"verified_records": records}))
    except (HymnalError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
