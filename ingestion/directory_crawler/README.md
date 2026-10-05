# Adventist Directory congregation crawler

Collects the public congregation results and detail pages from
[Adventist Directory](https://adventistdirectory.org). The default scope is the
**North American Division (`NAD`)**. On October 3, 2026 its search reported
**7,171 congregations**, including Churches, Companies, and Groups.

This is a local dataset collector, not a database importer. It does not alter
the app or deployed database. Confirm the directory owner's data-use terms and
your permission to republish these records before distribution.

## Install

From the infrastructure repository root:

```bash
python3 -m venv .venv-directory
.venv-directory/bin/python -m pip install -r ingestion/requirements-directory.txt
.venv-directory/bin/python -m playwright install chromium
```

Uses a separate environment so book-ingestion dependencies are unaffected.
macOS and Linux are supported (the single-writer lock uses `fcntl`).
The crawler requires Python 3.9 or newer.

## Start with a small sample

```bash
.venv-directory/bin/python -m ingestion.directory_crawler.cli crawl \
  --field-id NAD --max-pages 1 --max-details 2
```

The default opens a normal, visible Chromium window. If the website presents
security verification, complete it **manually** in that window; the crawler
waits up to two minutes and stops if access is not granted. No CAPTCHA solving,
stealth flags, proxy rotation, or alternate protected endpoints are used.

If necessary, prepare the saved browser session interactively:

```bash
.venv-directory/bin/python -m ingestion.directory_crawler.cli setup-browser \
  --field-id NAD --challenge-wait 300
```

Press Enter in the terminal when the directory loads. Use `--channel chrome`
on **all** browser commands if you prefer an installed Google Chrome. A
headless browser may still require verification even after interactive setup;
rerun visibly rather than trying to bypass it.

If verification continues to fail, the site may be refusing automated browser
access. Stop and request permission or an export from the directory owner.
Installing the crawler does not guarantee the site will authorize access.

## Full collection / background operation

```bash
.venv-directory/bin/python -m ingestion.directory_crawler.cli crawl --field-id NAD
```

It first discovers every listing page, then collects missing details. With
7,171 records and approximately 287 listing pages, the minimum navigation time
at the site's 20-second crawl delay is **about 41.5 hours**, plus loading time.
The machine must stay awake with network access.

For background operation on macOS or Linux, after any interactive setup:

```bash
mkdir -p data/adventist-directory
nohup .venv-directory/bin/python -u -m ingestion.directory_crawler.cli crawl \
  --field-id NAD --headless \
  > data/adventist-directory/crawler.log 2>&1 &
echo $!
```

Keep the printed PID. `tail -f data/adventist-directory/crawler.log` shows
progress. Stop gracefully with `kill -INT <PID>` or Ctrl+C for a foreground run.
Rerun the same command to resume. A security block or network error exits
nonzero with a clear message and retains progress; inspect the log before
restarting. There is no automatic unbounded retry loop.

Only one crawl/export may use an output directory at a time. Do not run
multiple output directories against this website concurrently: the
site-wide crawl delay applies across all scopes.

## Outputs and progress

By default, files are saved in `data/adventist-directory/` (Git-ignored):

- `checkpoint.sqlite3`: transactional page checkpoints and deduplicated detail
  records. This is a **local SQLite file**, not the app's PostgreSQL database.
- `NAD.json`: metadata plus a `congregations` array.
- `NAD.csv`: spreadsheet-friendly records (array fields are JSON strings).
- `NAD.status.json`: advertised count, discovered count, saved/pending details,
  missing coordinates, and explicit completeness status.
- `browser-profile/`: local browser session; treat it as private, do not commit
  or share it.

Each entity is deduplicated using the directory's stable `EntityID`, with an
app-ready ID such as `adventist-directory:14996`. Fields include name, type,
city, state/province, country, physical/mailing address lines, coordinates and
their stated source, website, public phone, service times, language, membership,
conference/union/division identifiers, source URL, and detail-fetch timestamp.
Personnel names and email addresses are deliberately excluded.

Missing geography is **null**, never fabricated or automatically geocoded.
Directory coordinates may be address-derived or stale; preserve their
provenance. The existing app table requires non-null coordinates: a later
import must explicitly handle records without them.

Exports run every 25 saved details and on normal completion/interruption/error.
Pending listing-only records have `detail_status: "pending"`; do not treat them
as full church records. `metadata.complete` becomes true only when the entire
listing was enumerated and every discovered record has saved details.
JSON preserves original strings; CSV prefixes formula-like strings with an
apostrophe to prevent spreadsheet formula execution.

Read progress during a crawl without acquiring its writer lock:

```bash
cat data/adventist-directory/NAD.status.json
```

When no crawler is running:

```bash
.venv-directory/bin/python -m ingestion.directory_crawler.cli status --field-id NAD
.venv-directory/bin/python -m ingestion.directory_crawler.cli export --field-id NAD
```

`--max-pages` and `--max-details` limit work **in this invocation**, not the
entire dataset. `0` skips that stage, and `-1` (default) is unlimited.
For listing-only discovery use `--max-details 0`; for processing previously
discovered details use `--max-pages 0`.

## Expand to more divisions or unions

Find the organization's `AdmFieldID` in its directory link, for example
`ViewAdmField.aspx?AdmFieldID=SCIC`. Run scopes sequentially with the same output
directory to reuse overlapping congregation details:

```bash
.venv-directory/bin/python -m ingestion.directory_crawler.cli crawl --field-id SCIC
.venv-directory/bin/python -m ingestion.directory_crawler.cli crawl --field-id IAD
```

The all-world scope is `GC`, but it is a much larger crawl. Each scope has its
own JSON/CSV/status exports; shared entity IDs are fetched only once. This
collector is a resumable snapshot, not a periodic refresh service. For a fresh
snapshot use `--output data/adventist-directory-2027`; add custom output paths
to your ignore rules.

## Access policy and completeness

The crawler reads `robots.txt` each run, refuses unverified/disallowed access,
uses at least its 20-second delay between top-level navigations, and honors a
larger configured/published delay. It does not follow `/email*`, `/export*`,
embedded map pages, congregation websites, or other hosts. Browser page assets
load normally; the delay governs document navigations.

Page ranges, scope, pagination advancement, totals, and unique entity counts
are checked. A changed total or overlapping/skipped records stops collection
instead of claiming a complete dataset. Since the directory has no snapshot
API, source changes that preserve the count cannot be fully detected; compare
against a fresh snapshot when completeness is important.

## Tests

```bash
.venv-directory/bin/python -m unittest discover -s ingestion/tests \
  -p test_directory_crawler.py -v
```
