import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

BASE_URL = "https://adventistdirectory.org"


class DirectoryError(RuntimeError):
    pass


def text(node: Tag) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def source_url(url: str) -> str:
    parsed = urlparse(urljoin(BASE_URL, url))
    if (
        parsed.scheme != "https"
        or parsed.netloc != "adventistdirectory.org"
        or parsed.path.lower()
        not in ("/robots.txt", "/searchresults.aspx", "/default.aspx", "/viewentity.aspx")
        or parsed.fragment
    ):
        raise DirectoryError("Unexpected directory URL: " + url)
    return parsed.geturl()


def search_url(field_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", field_id):
        raise DirectoryError("Field ID must contain only letters, numbers, _ or -.")
    return BASE_URL + "/SearchResults.aspx?EntityType=C&AdmFieldID=" + field_id + "&SortBy=0"


def validate_scope_url(url: str, field_id: str) -> None:
    parsed = urlparse(source_url(url))
    query = parse_qs(parsed.query)
    if (
        parsed.path.lower() not in ("/searchresults.aspx", "/default.aspx")
        or query.get("EntityType") != ["C"]
        or query.get("AdmFieldID") != [field_id]
        or query.get("SortBy") != ["0"]
        or (parsed.path.lower() == "/default.aspx" and query.get("page") != ["searchresults"])
    ):
        raise DirectoryError("Pagination changed the requested congregation scope: " + url)


def validate_destination(requested: str, actual: str) -> None:
    original = urlparse(source_url(requested))
    destination = urlparse(source_url(actual))
    query = parse_qs(original.query)
    final_query = parse_qs(destination.query)
    if original.path.lower() == "/robots.txt":
        valid = destination.path.lower() == "/robots.txt" and not destination.query
    elif original.path.lower() == "/viewentity.aspx":
        valid = (
            destination.path.lower() == "/viewentity.aspx"
            and final_query.get("EntityID") == query.get("EntityID")
        )
    else:
        validate_scope_url(actual, query["AdmFieldID"][0])
        valid = final_query.get("PageIndex", ["0"]) == query.get("PageIndex", ["0"])
    if not valid:
        raise DirectoryError("Directory redirected to an unexpected page: " + actual)


def number(value: str) -> Optional[int]:
    value = value.replace(",", "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise DirectoryError("Unexpected membership count: " + value)
    return int(value)


@dataclass
class SearchPage:
    total: int
    start: int
    end: int
    records: List[Dict[str, Any]]
    next_url: Optional[str]


def parse_search(html: str, url: str, field_id: str) -> SearchPage:
    validate_scope_url(url, field_id)
    soup = BeautifulSoup(html, "html.parser")
    match = re.search(
        r"Total Matches:\s*([\d,]+)\s+Displaying\s*\(([\d,]+)\s*-\s*([\d,]+)\)",
        soup.get_text(" ", strip=True),
    )
    if not match:
        raise DirectoryError("Search counts missing; page may be blocked or its layout changed.")
    total, start, end = (int(part.replace(",", "")) for part in match.groups())
    records = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 4 or not text(cells[0]).isdigit():
            continue
        link = cells[1].find("a", href=re.compile(r"^/?ViewEntity\.aspx\?EntityID=\d+$", re.I))
        if link is None:
            raise DirectoryError("Congregation row has no entity link.")
        detail_url = source_url(str(link["href"]))
        entity_id = parse_qs(urlparse(detail_url).query)["EntityID"][0]
        record: Dict[str, Any] = {
            "church_id": "adventist-directory:" + entity_id,
            "source_entity_id": entity_id,
            "source_url": detail_url,
            "name": text(link),
            "members": number(text(cells[2])),
            "lat": None,
            "lon": None,
        }
        for key, suffix in (
            ("city", "lblCity"),
            ("state_province", "lblStateProv"),
            ("country", "lblCountry"),
            ("congregation_type", "lblTypeDesc"),
        ):
            node = row.find(id=re.compile(re.escape(suffix) + "$"))
            if node is None:
                raise DirectoryError("Listing field missing: " + suffix)
            record[key] = text(node).rstrip(",").strip() or None
        location = cells[1].find("div")
        record["location_raw"] = text(location) if location else None
        website = cells[1].find("a", href=re.compile(r"^https?://", re.I))
        record["website"] = str(website["href"]) if website else None
        conference = cells[3].find("em")
        record["conference"] = text(conference) if conference else None
        records.append(record)
    expected = end - start + 1 if total else 0
    if len(records) != expected or len({r["source_entity_id"] for r in records}) != len(records):
        raise DirectoryError("Search page row count or entity IDs do not match its displayed range.")
    if total and (start < 1 or end > total or end < start):
        raise DirectoryError("Invalid search result range.")
    next_links = soup.find_all("a", string=re.compile(r"^\s*Next\s*\(", re.I))
    next_url = source_url(str(next_links[0]["href"])) if next_links else None
    if next_url:
        validate_scope_url(next_url, field_id)
        current_index = int(parse_qs(urlparse(url).query).get("PageIndex", ["0"])[0])
        next_index = parse_qs(urlparse(next_url).query).get("PageIndex", [])
        if next_index != [str(current_index + 1)]:
            raise DirectoryError("Pagination does not advance exactly one page.")
    if (end < total) != bool(next_url):
        raise DirectoryError("Next-page link does not match the advertised total.")
    return SearchPage(total, start, end, records, next_url)


def lines(node: Optional[Tag]) -> List[str]:
    if node is None:
        return []
    for unwanted in node.find_all(["script", "style", "img"]):
        unwanted.decompose()
    return [part.strip() for part in node.get_text("\n", strip=True).splitlines() if part.strip()]


def parse_detail(html: str, listing: Dict[str, Any], fetched_at: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    identity = re.search(r"OMEntityID:\s*(\d+)", soup.get_text(" ", strip=True))
    if identity is None or identity.group(1) != listing["source_entity_id"]:
        raise DirectoryError("Detail page is blocked, changed, or belongs to another entity.")
    fields: Dict[str, Tag] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) == 2:
            label = text(cells[0]).rstrip(":").lower()
            if text(cells[0]).endswith(":"):
                fields[label] = cells[1]
    if "type" not in fields:
        raise DirectoryError("Detail page has no congregation type.")
    result = dict(listing)
    result.update(
        {
            "detail_type": text(fields["type"]),
            "address_lines": lines(fields.get("street address") or fields.get("address")),
            "mailing_address_lines": lines(fields.get("mail")),
            "phone": text(fields["phone"]) if "phone" in fields else None,
            "services": lines(fields.get("services")),
            "language": text(fields["language"]) if "language" in fields else None,
            "directions": text(fields["directions"]) if "directions" in fields else None,
            "lat": None,
            "lon": None,
            "coordinate_source": None,
            "fetched_at": fetched_at,
        }
    )
    if "members" in fields:
        result["members"] = number(text(fields["members"]))
    if "website" in fields:
        website = fields["website"].find("a", href=re.compile(r"^https?://", re.I))
        result["website"] = str(website["href"]) if website else result["website"]
    for key in ("conference", "union", "division"):
        node = fields.get(key)
        link = node.find("a", href=re.compile(r"ViewAdmField\.aspx\?AdmFieldID=", re.I)) if node else None
        if link:
            result[key] = text(link)
            result[key + "_id"] = parse_qs(urlparse(str(link["href"])).query)["AdmFieldID"][0]
    if "lat/lon" in fields:
        coordinate_text = text(fields["lat/lon"])
        match = re.search(
            r"Latitude:\s*(-?\d+(?:\.\d+)?),\s*Longitude:\s*(-?\d+(?:\.\d+)?)",
            coordinate_text,
        )
        if match:
            lat, lon = (float(value) for value in match.groups())
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise DirectoryError("Out-of-range coordinates for " + listing["source_entity_id"])
            result["lat"], result["lon"] = lat, lon
        elif re.search(r"Latitude:\s*-?\d|Longitude:\s*-?\d", coordinate_text):
            raise DirectoryError("Unrecognized coordinate format for " + listing["source_entity_id"])
        source = re.search(r"Source:\s*(.*)", coordinate_text)
        result["coordinate_source"] = source.group(1) if source else None
    return result
