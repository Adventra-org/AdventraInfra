import argparse
import fcntl
import json
import logging
import math
import signal
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional
from urllib.parse import urlparse

from protego import Protego

from .parser import (
    BASE_URL, DirectoryError, parse_detail, parse_search, source_url, validate_destination,
)
from .store import Store, timestamp

if TYPE_CHECKING:
    from playwright.sync_api import Page, Route

LOG = logging.getLogger("adventra.directory")
CRAWLER_AGENT = "AdventraDirectoryCollector"


@contextmanager
def exclusive_run(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".crawler.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DirectoryError("Another crawler/export is using this output directory.") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Browser:
    def __init__(self, page: "Page", store: Store, delay: float, challenge_wait: float):
        self.page = page
        self.store = store
        self.delay = max(20.0, delay)
        self.challenge_wait = challenge_wait
        self.robots: Optional[Protego] = None

    def wait(self) -> None:
        remaining = self.store.last_request() + self.delay - time.time()
        if remaining > 0:
            time.sleep(remaining)
        self.store.set_last_request(time.time())

    def fetch(self, url: str, robots_request: bool = False) -> str:
        url = source_url(url)
        if not robots_request and (self.robots is None or not self.robots.can_fetch(url, CRAWLER_AGENT)):
            raise DirectoryError("robots.txt does not permit fetching " + url)
        self.wait()
        LOG.info("Fetching %s", url)
        response = self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if response is None:
            raise DirectoryError("No response for " + url)
        if response.status == 429 or response.status >= 500:
            retry_after = response.headers.get("retry-after", "")
            wait_seconds = retry_delay(retry_after)
            self.store.set_last_request(time.time() + wait_seconds)
            raise DirectoryError(
                "HTTP %s for %s; saved cooldown of %.0fs. Resume later."
                % (response.status, url, wait_seconds)
            )
        blocked = self.page.title().lower()
        if any(word in blocked for word in ("just a moment", "access denied", "attention required")):
            LOG.warning(
                "Browser verification encountered. If the browser is visible, complete it manually. "
                "Waiting up to %.0fs; no automated challenge solving.", self.challenge_wait
            )
            self.page.wait_for_function(
                """() => !/just a moment|access denied|attention required/i.test(document.title)""",
                timeout=self.challenge_wait * 1000,
            )
            # A successful normal browser navigation may follow the initial 403 challenge.
        elif response.status >= 400:
            raise DirectoryError("HTTP %s for %s; collection stopped." % (response.status, url))
        marker = (
            r"user-agent:"
            if robots_request
            else r"OMEntityID:\s*\d+"
            if urlparse(url).path.lower() == "/viewentity.aspx"
            else r"Total Matches:\s*[\d,]+"
        )
        # A verification redirect can clear the title before its new document is ready.
        self.page.wait_for_function(
            """pattern => document.body && new RegExp(pattern, 'i').test(document.body.innerText)""",
            arg=marker, timeout=60000,
        )
        validate_destination(url, self.page.url)
        if robots_request:
            body = self.page.locator("body").inner_text()
            if not any(line.lower().strip().startswith("user-agent:") for line in body.splitlines()):
                raise DirectoryError("robots.txt could not be verified; collection stopped.")
            return body
        return self.page.content()

    def load_robots(self) -> None:
        contents = self.fetch(BASE_URL + "/robots.txt", robots_request=True)
        rules = Protego.parse(contents)
        published_delay = float(rules.crawl_delay(CRAWLER_AGENT) or 20)
        if not math.isfinite(published_delay) or published_delay < 0:
            raise DirectoryError("Invalid crawl delay in robots.txt; collection stopped.")
        self.delay = max(self.delay, published_delay)
        self.robots = rules
        self.page.route("**/*", self.apply_robots)
        LOG.info("Navigation interval: %.0fs (respecting robots.txt).", self.delay)

    def apply_robots(self, route: "Route") -> None:
        if (
            urlparse(route.request.url).netloc == "adventistdirectory.org"
            and self.robots is not None
            and not self.robots.can_fetch(route.request.url, CRAWLER_AGENT)
        ):
            route.abort()
        else:
            route.continue_()


def retry_delay(value: str) -> float:
    from email.utils import parsedate_to_datetime

    if value.isdigit():
        return max(60.0, float(value))
    if value:
        try:
            return max(60.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            LOG.warning("Invalid Retry-After header %r; using a five-minute cooldown.", value)
    return 300.0


def collect(browser: Browser, store: Store, field_id: str, max_pages: int, max_details: int) -> None:
    pages = 0
    while store.scope(field_id)["next_url"] and (max_pages < 0 or pages < max_pages):
        url = store.scope(field_id)["next_url"]
        page = parse_search(browser.fetch(url), url, field_id)
        store.save_page(field_id, page)
        pages += 1
        LOG.info("Saved listing page: %s", json.dumps(store.status(field_id)))
    details = 0
    while max_details < 0 or details < max_details:
        listing = store.pending(field_id)
        if listing is None:
            break
        record = parse_detail(browser.fetch(listing["source_url"]), listing, timestamp())
        store.save_detail(record)
        details += 1
        LOG.info("Saved %s (%s): %s", record["name"], record["source_entity_id"],
                 json.dumps(store.status(field_id)))
        if details % 25 == 0:
            store.export(field_id)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resumable public Adventist Directory crawler.")
    parser.add_argument("command", choices=("crawl", "status", "export", "setup-browser"))
    parser.add_argument("--field-id", default="NAD", help="Division/union/conference AdmFieldID (default NAD).")
    parser.add_argument("--output", type=Path, default=Path("data/adventist-directory"))
    parser.add_argument("--delay", type=float, default=20, help="Seconds between navigations; minimum 20.")
    parser.add_argument("--max-pages", type=int, default=-1, help="Pages this run; 0 skips listing, -1 unlimited.")
    parser.add_argument("--max-details", type=int, default=-1, help="Details this run; 0 skips details, -1 unlimited.")
    parser.add_argument("--headless", action="store_true", help="No browser window; stop if verification is needed.")
    parser.add_argument("--channel", choices=("chrome", "msedge"), help="Use an installed browser instead of Chromium.")
    parser.add_argument("--challenge-wait", type=float, default=120, help="Manual verification wait, seconds.")
    args = parser.parse_args()
    if (
        not math.isfinite(args.delay) or args.delay < 20
        or not math.isfinite(args.challenge_wait) or args.challenge_wait <= 0
        or args.max_pages < -1 or args.max_details < -1
    ):
        parser.error("delay must be finite and >=20; challenge-wait must be positive; limits must be >=-1.")
    if args.command == "setup-browser" and args.headless:
        parser.error("setup-browser requires a visible browser.")
    return args


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    args = arguments()
    # Validate the field ID before creating files or opening a browser.
    from .parser import search_url

    directory = args.output.expanduser().resolve()
    try:
        search_url(args.field_id)
        with exclusive_run(directory):
            store = Store(directory)
            try:
                store.add_scope(args.field_id)
                if args.command in ("status", "export"):
                    status = store.export(args.field_id) if args.command == "export" else store.status(args.field_id)
                    print(json.dumps(status, indent=2))
                    return 0
                if args.command == "crawl" and store.status(args.field_id)["complete"]:
                    print(json.dumps(store.export(args.field_id), indent=2))
                    return 0
                from playwright.sync_api import Error as PlaywrightError, sync_playwright

                profile = directory / "browser-profile"
                profile.mkdir(mode=0o700, exist_ok=True)
                try:
                    with sync_playwright() as playwright:
                        context = playwright.chromium.launch_persistent_context(
                            str(profile), headless=args.headless, channel=args.channel,
                            accept_downloads=False,
                        )
                        try:
                            page = context.pages[0] if context.pages else context.new_page()
                            browser = Browser(page, store, args.delay, args.challenge_wait)
                            browser.load_robots()
                            if args.command == "setup-browser":
                                browser.fetch(search_url(args.field_id))
                                parse_search(page.content(), search_url(args.field_id), args.field_id)
                                input("Directory loaded. Press Enter to save the browser session and close: ")
                            else:
                                collect(browser, store, args.field_id, args.max_pages, args.max_details)
                        finally:
                            context.close()
                except PlaywrightError as error:
                    raise DirectoryError(
                        "Browser/network error: %s. Checkpoint preserved. Use setup-browser "
                        "without --headless for manual verification, then rerun." % error
                    ) from error
                print(json.dumps(store.export(args.field_id), indent=2))
                return 0
            finally:
                # Export even on interruption/error; partial datasets are explicitly marked.
                store.export(args.field_id)
                store.close()
    except KeyboardInterrupt:
        LOG.warning("Interrupted. Checkpoint and partial exports preserved; rerun to resume.")
        return 130
    except (DirectoryError, OSError, sqlite3.Error) as error:
        LOG.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
