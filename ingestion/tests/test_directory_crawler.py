import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ingestion.directory_crawler.cli import Browser, collect, exclusive_run, retry_delay
from ingestion.directory_crawler.parser import (
    DirectoryError, parse_detail, parse_search, search_url, source_url, validate_destination,
)
from ingestion.directory_crawler.store import Store


def listing_html(entity="123", total=1, start=1, next_link=""):
    return """
    <p>Total Matches: %s Displaying (%s - %s)</p>
    <table><tr><td>%s</td><td>
    <a href="/ViewEntity.aspx?EntityID=%s">Example Church</a>
    <a href="https://example.org">website</a>
    <div><span id="prefix_lblCity">Example City,</span>
    <span id="prefix_lblStateProv">BC,</span>
    <span id="prefix_lblCountry">Canada</span></div></td>
    <td>1,234</td><td><span id="prefix_lblTypeDesc">Company</span>
    <em><a href="/ViewEntity.aspx?EntityID=999">Example Conference</a></em>
    </td></tr></table>%s
    """ % (total, start, start, start, entity, next_link)


DETAIL = """
<p>OMEntityID: 123 OrgMastID: EXAMPLE</p>
<table>
<tr><td>Street Address:</td><td><img alt="Photo Needed">1 Main Street<br>Example City BC<br>Canada</td></tr>
<tr><td>Mail:</td><td>PO Box 12<br>Canada</td></tr>
<tr><td>Type:</td><td>Company (CGR)</td></tr>
<tr><td>Lat/Lon:</td><td>Latitude: 51.682900, Longitude: -121.309000, Source: Address</td></tr>
<tr><td>Services:</td><td>Sabbath School: 10:00 am<br>Church: 11:00 am</td></tr>
<tr><td>Division:</td><td><a href="ViewAdmField.aspx?AdmFieldID=NAD">North American Division</a> (website)</td></tr>
<tr><td>Website:</td><td><a href="https://church.example">website</a></td></tr>
<tr><td>Pastor:</td><td>Do not collect personnel</td></tr>
<tr><td>Email:</td><td>Do not collect email</td></tr>
</table>
"""


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.url = search_url("NAD")
        self.page = parse_search(listing_html(), self.url, "NAD")

    def test_listing_ignores_conference_entity_and_preserves_company(self):
        self.assertEqual(len(self.page.records), 1)
        record = self.page.records[0]
        self.assertEqual(record["source_entity_id"], "123")
        self.assertEqual(record["city"], "Example City")
        self.assertEqual(record["congregation_type"], "Company")
        self.assertEqual(record["members"], 1234)

    def test_detail_fields_coordinates_and_provenance(self):
        record = parse_detail(DETAIL, self.page.records[0], "2026-01-01T00:00:00Z")
        self.assertEqual(record["address_lines"], ["1 Main Street", "Example City BC", "Canada"])
        self.assertEqual(record["lat"], 51.6829)
        self.assertEqual(record["lon"], -121.309)
        self.assertEqual(record["division_id"], "NAD")
        self.assertEqual(record["coordinate_source"], "Address")
        self.assertEqual(len(record["services"]), 2)
        self.assertEqual(record["website"], "https://church.example")
        self.assertNotIn("pastor", record)
        self.assertNotIn("email", record)
        self.assertEqual(record["church_id"], "adventist-directory:123")

    def test_missing_coordinates_remain_null(self):
        html = DETAIL.replace(
            "<tr><td>Lat/Lon:</td><td>Latitude: 51.682900, Longitude: -121.309000, Source: Address</td></tr>", ""
        )
        self.assertIsNone(parse_detail(html, self.page.records[0], "now")["lat"])
        blank = DETAIL.replace("51.682900", "").replace("-121.309000", "")
        self.assertIsNone(parse_detail(blank, self.page.records[0], "now")["lat"])

    def test_coordinates_must_be_in_range(self):
        with self.assertRaises(DirectoryError):
            parse_detail(DETAIL.replace("51.682900", "151.682900"), self.page.records[0], "now")

    def test_wrong_entity_and_challenges_rejected(self):
        for html in ("<title>Just a moment...</title>", DETAIL.replace("OMEntityID: 123", "OMEntityID: 124")):
            with self.assertRaises(DirectoryError):
                parse_detail(html, self.page.records[0], "now")
        with self.assertRaises(DirectoryError):
            parse_search("<title>Just a moment...</title>", self.url, "NAD")

    def test_pagination_preserves_scope_and_advances(self):
        link = '<a href="/default.aspx?page=searchresults&amp;EntityType=C&amp;AdmFieldID=NAD&amp;SortBy=0&amp;PageIndex=1">Next (2 - 2)</a>'
        page = parse_search(listing_html(total=2, next_link=link), self.url, "NAD")
        self.assertIn("PageIndex=1", page.next_url)
        for broken in (link.replace("NAD", "GC"), link.replace("PageIndex=1", "PageIndex=0"), ""):
            with self.assertRaises(DirectoryError):
                parse_search(listing_html(total=2, next_link=broken), self.url, "NAD")

    def test_unknown_layout_and_missing_fields_fail(self):
        with self.assertRaises(DirectoryError):
            parse_search(listing_html().replace("prefix_lblCountry", "unknown"), self.url, "NAD")
        with self.assertRaises(DirectoryError):
            parse_search(listing_html().replace("(1 - 1)", "(1 - 2)"), self.url, "NAD")

    def test_empty_scope(self):
        page = parse_search("<p>Total Matches: 0 Displaying (0 - 0)</p>", self.url, "NAD")
        self.assertEqual(page.records, [])
        self.assertIsNone(page.next_url)

    def test_urls_cannot_escape_directory_or_use_disallowed_endpoints(self):
        for url in ("https://example.org", "/export.aspx", "/email.aspx", "/Admin", "//evil.example/ViewEntity.aspx"):
            with self.assertRaises(DirectoryError):
                source_url(url)
        with self.assertRaises(DirectoryError):
            search_url("NAD&EntityType=X")

    def test_redirect_cannot_change_scope_or_page(self):
        for actual in (search_url("GC"), search_url("NAD") + "&PageIndex=1"):
            with self.assertRaises(DirectoryError):
                validate_destination(search_url("NAD"), actual)
        validate_destination(
            search_url("NAD"),
            "https://adventistdirectory.org/default.aspx?page=searchresults&EntityType=C&AdmFieldID=NAD&SortBy=0",
        )


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.store = Store(self.path)
        self.store.add_scope("NAD")
        self.page = parse_search(listing_html(), search_url("NAD"), "NAD")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_resume_after_reopen_and_complete_exports(self):
        self.store.save_page("NAD", self.page)
        self.assertFalse(self.store.status("NAD")["complete"])
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.pending("NAD")["source_entity_id"], "123")
        self.store.save_detail(parse_detail(DETAIL, self.store.pending("NAD"), "now"))
        self.assertIsNone(self.store.pending("NAD"))
        self.assertTrue(self.store.export("NAD")["complete"])
        data = json.loads((self.path / "NAD.json").read_text())
        self.assertEqual(len(data["congregations"]), 1)
        self.assertEqual(data["congregations"][0]["detail_status"], "complete")
        self.assertIn("-121.309", (self.path / "NAD.csv").read_text())

    def test_partial_exports_do_not_claim_completion(self):
        self.store.save_page("NAD", self.page)
        self.store.export("NAD")
        data = json.loads((self.path / "NAD.json").read_text())
        self.assertFalse(data["metadata"]["complete"])
        self.assertEqual(data["congregations"][0]["detail_status"], "pending")

    def test_overlapping_scopes_share_details_without_duplicates(self):
        self.store.save_page("NAD", self.page)
        self.store.save_detail(parse_detail(DETAIL, self.page.records[0], "now"))
        self.store.add_scope("SCIC")
        self.store.save_page("SCIC", self.page)
        self.assertIsNone(self.store.pending("SCIC"))
        self.assertEqual(self.store.status("SCIC")["details_saved"], 1)

    def test_count_change_is_rejected_without_advancing(self):
        next_link = '<a href="/default.aspx?page=searchresults&amp;EntityType=C&amp;AdmFieldID=NAD&amp;SortBy=0&amp;PageIndex=1">Next (2 - 2)</a>'
        self.store.save_page("NAD", parse_search(listing_html(total=2, next_link=next_link), search_url("NAD"), "NAD"))
        changed = self.page
        changed.total = 3
        changed.start = 2
        with self.assertRaises(DirectoryError):
            self.store.save_page("NAD", changed)
        self.assertEqual(self.store.status("NAD")["pages_saved"], 1)

    def test_bounded_run_then_resume_skips_saved_requests(self):
        browser = Mock()
        browser.fetch.side_effect = [listing_html(), DETAIL]
        collect(browser, self.store, "NAD", 1, 1)
        self.assertEqual(browser.fetch.call_count, 2)
        collect(browser, self.store, "NAD", -1, -1)
        self.assertEqual(browser.fetch.call_count, 2)

    def test_failed_detail_is_pending_and_retryable(self):
        self.store.save_page("NAD", self.page)
        browser = Mock()
        browser.fetch.return_value = "<title>Access denied</title>"
        with self.assertRaises(DirectoryError):
            collect(browser, self.store, "NAD", 0, 1)
        self.assertEqual(self.store.status("NAD")["details_pending"], 1)

    def test_single_writer_lock(self):
        with exclusive_run(self.path):
            with self.assertRaises(DirectoryError):
                with exclusive_run(self.path):
                    pass

    def test_delay_persists_across_restarts(self):
        self.store.set_last_request(100)
        browser = Browser(Mock(), self.store, 20, 120)
        with patch("ingestion.directory_crawler.cli.time.time", return_value=105), patch(
            "ingestion.directory_crawler.cli.time.sleep"
        ) as sleep:
            browser.wait()
        sleep.assert_called_once_with(15)
        self.assertEqual(self.store.last_request(), 105)

    def test_robots_disallowed_url_never_requested(self):
        browser = Browser(Mock(), self.store, 20, 120)
        browser.robots = Mock()
        browser.robots.can_fetch.return_value = False
        with self.assertRaises(DirectoryError):
            browser.fetch(self.page.records[0]["source_url"])
        browser.page.goto.assert_not_called()

    def test_retry_after(self):
        self.assertEqual(retry_delay("600"), 600)
        self.assertEqual(retry_delay("invalid"), 300)

    def test_plaintext_robots_does_not_require_a_title_element(self):
        page = Mock()
        page.goto.return_value.status = 200
        page.title.return_value = ""
        page.url = "https://adventistdirectory.org/robots.txt"
        page.locator.return_value.inner_text.return_value = (
            "User-agent: *\nCrawl-delay: 25\nDisallow: /export*\n"
        )
        browser = Browser(page, self.store, 20, 120)
        with patch.object(browser, "wait"):
            browser.load_robots()
        self.assertEqual(browser.delay, 25)
        self.assertFalse(browser.robots.can_fetch("https://adventistdirectory.org/export.aspx", "AdventraDirectoryCollector"))
        page.title.assert_called_once()
        page.wait_for_function.assert_called_once()

    def test_http_rate_limit_saves_cooldown_without_marking_success(self):
        page = Mock()
        page.goto.return_value.status = 429
        page.goto.return_value.headers = {"retry-after": "600"}
        browser = Browser(page, self.store, 20, 120)
        browser.robots = Mock()
        browser.robots.can_fetch.return_value = True
        with patch.object(browser, "wait"), patch(
            "ingestion.directory_crawler.cli.time.time", return_value=100
        ):
            with self.assertRaises(DirectoryError):
                browser.fetch(self.page.records[0]["source_url"])
        self.assertEqual(self.store.last_request(), 700)
        page.content.assert_not_called()

    def test_embedded_disallowed_requests_are_blocked(self):
        browser = Browser(Mock(), self.store, 20, 120)
        browser.robots = Mock()
        browser.robots.can_fetch.return_value = False
        route = Mock()
        route.request.url = "https://adventistdirectory.org/default.aspx?iframe=1"
        browser.apply_robots(route)
        route.abort.assert_called_once()
        route.continue_.assert_not_called()

    def test_incomplete_checkpoint_keeps_next_page_after_reopen(self):
        next_link = '<a href="/default.aspx?page=searchresults&amp;EntityType=C&amp;AdmFieldID=NAD&amp;SortBy=0&amp;PageIndex=1">Next (2 - 2)</a>'
        first = parse_search(listing_html(total=2, next_link=next_link), search_url("NAD"), "NAD")
        self.store.save_page("NAD", first)
        self.store.close()
        self.store = Store(self.path)
        next_url = self.store.scope("NAD")["next_url"]
        second = parse_search(listing_html(entity="124", total=2, start=2), next_url, "NAD")
        self.store.save_page("NAD", second)
        self.assertEqual(self.store.status("NAD")["discovered"], 2)
        self.assertTrue(self.store.status("NAD")["listing_complete"])
        self.assertFalse(self.store.status("NAD")["complete"])

    def test_csv_escapes_formula_strings_but_json_preserves_source(self):
        self.page.records[0]["name"] = "=unsafe()"
        self.store.save_page("NAD", self.page)
        self.store.export("NAD")
        self.assertIn("'=unsafe()", (self.path / "NAD.csv").read_text())
        data = json.loads((self.path / "NAD.json").read_text())
        self.assertEqual(data["congregations"][0]["name"], "=unsafe()")


if __name__ == "__main__":
    unittest.main()
