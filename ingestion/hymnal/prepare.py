import argparse
import json
from pathlib import Path

from .cli import HEADING, HymnalError, integer, object_value, read_bytes, text, unique_object


def category_map(ranges: object) -> dict[int, str]:
    if not isinstance(ranges, list) or not ranges:
        raise HymnalError("Category ranges must be a nonempty array.")
    categories: dict[int, str] = {}
    for item in ranges:
        if not isinstance(item, list) or len(item) != 3:
            raise HymnalError("Each category range requires start, end and label.")
        start = integer(item[0], "category start", 1, 695)
        end = integer(item[1], "category end", start, 695)
        label = text(item[2], "category", 120)
        for number in range(start, end + 1):
            if number in categories:
                raise HymnalError(f"Overlapping category ranges at hymn {number}.")
            categories[number] = label
    if set(categories) != set(range(1, 696)):
        raise HymnalError("Category ranges must cover exactly hymns 1-695.")
    return categories


def prepare(raw_dir: Path) -> dict[str, object]:
    raw_dir = raw_dir.resolve()
    if not raw_dir.is_dir():
        raise HymnalError("raw-dir must be an existing local directory.")
    category_path = Path(__file__).with_name("categories.json")
    source = object_value(
        json.loads(category_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object),
        {"source_uri", "note", "ranges"}, "category source",
    )
    categories = category_map(source.get("ranges"))
    hymns = []
    for number in range(1, 696):
        filename = f"{number:03}.txt"
        path = (raw_dir / filename).resolve()
        if not path.is_relative_to(raw_dir):
            raise HymnalError(f"Source file resolves outside raw-dir: {filename}.")
        content = read_bytes(path, 101024).decode("utf-8-sig")
        lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        index = next((i for i, line in enumerate(lines) if line.strip()), None)
        heading = HEADING.fullmatch(lines[index]) if index is not None else None
        if heading is None or int(heading.group(1)) != number:
            raise HymnalError(f"Invalid/mismatched numbered heading in {filename}.")
        text(heading.group(2), "title", 500)
        text("\n".join(lines[index + 1:]).strip("\n"), "lyrics", 100000)
        hymns.append({
            "file": filename, "hymn_id": f"sdah-{number:03}", "number": number,
            "language": "en", "category": categories[number],
            "copyright_notice": "",
            "rights": {"status": "pending", "basis": None, "reference": ""},
        })
    return {
        "revision": 1,
        "source_uri": "https://github.com/ariseforgod/adventist_hymnal/tree/7e3748ab3062872b675cdf769ad244ce5e6182b7/raw_text",
        "approval_reference": "",
        "complete_catalog": True,
        "languages": ["en"],
        "hymns": hymns,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print English SDA hymnal review metadata; never approves rights or emits lyrics."
    )
    parser.add_argument("--raw-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        metadata = prepare(args.raw_dir)
        print(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False))
    except (HymnalError, OSError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
