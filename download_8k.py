"""Download all available Massive 8-K disclosures using the key in .env.

Run: python3 download_8k.py
Writes raw JSONL, CSV, and a summary in data/. No third-party packages needed.
One row is one disclosure, not necessarily one distinct filing.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://api.massive.com/stocks/filings/8-K/vX/disclosures"
FIELDS = [
    "accession_number", "cik", "filing_date", "filing_url",
    "primary_category", "secondary_category", "tertiary_category",
    "supporting_text", "tickers",
]


def load_key():
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "MASSIVE_API_KEY":
                key = value.strip().strip("\"'")
    if not key or key == "your-key-here":
        raise RuntimeError("Set MASSIVE_API_KEY in the environment or .env.")
    return key


def get_page(url, key):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "api.massive.com":
        raise RuntimeError("Unexpected pagination host; stopped before sending credentials.")
    for attempt in range(8):
        try:
            request = Request(url, headers={"Authorization": f"Bearer {key}"})
            with urlopen(request, timeout=60) as response:
                page = json.load(response)
            if page.get("status") != "OK" or not isinstance(page.get("results"), list):
                raise RuntimeError("API returned an unsuccessful or malformed page.")
            return page
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 7:
                raise RuntimeError(f"Massive API returned HTTP {exc.code}.") from None
            retry = exc.headers.get("Retry-After", "")
            delay = float(retry) if retry.isdigit() else min(2 ** attempt, 60)
            print(f"HTTP {exc.code}; retrying in {delay:g} seconds.", flush=True)
            time.sleep(delay)
        except URLError:
            raise RuntimeError("Cannot connect to api.massive.com. Check network/DNS access.") from None


def export_csv(raw_path, output_path):
    fields = list(FIELDS)
    count = 0
    filings = set()
    first_date = last_date = None
    with raw_path.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            fields.extend(k for k in row if k not in fields)
            count += 1
            if row.get("accession_number"):
                filings.add(row["accession_number"])
            date = row.get("filing_date")
            if date:
                first_date = min(first_date or date, date)
                last_date = max(last_date or date, date)
    with raw_path.open(encoding="utf-8") as source, output_path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for line in source:
            row = json.loads(line)
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})
    return {"rows": count, "columns": len(fields), "column_names": fields,
            "unique_filings": len(filings), "earliest_filing_date": first_date,
            "latest_filing_date": last_date}


def main():
    key = load_key()
    url = ENDPOINT + "?limit=1000&sort=filing_date.asc"
    # Fetch first before creating files, so a connection failure leaves no empty dataset.
    page = get_page(url, key)
    output = ROOT / "data"
    output.mkdir(exist_ok=True)
    raw = output / "8k_disclosures.jsonl.partial"
    seen = set()
    count = pages = 0
    with raw.open("w", encoding="utf-8") as target:
        while True:
            if url in seen:
                raise RuntimeError("Pagination repeated a URL; incomplete data remains in .partial.")
            seen.add(url)
            for row in page["results"]:
                if not isinstance(row, dict):
                    raise RuntimeError("Malformed disclosure record.")
                target.write(json.dumps(row, ensure_ascii=False) + "\n")
                count += 1
            target.flush()
            pages += 1
            print(f"Page {pages}: {count:,} disclosures downloaded", flush=True)
            url = page.get("next_url")
            if not url:
                break
            page = get_page(url, key)
    csv_partial = output / "8k_disclosures.csv.partial"
    summary = export_csv(raw, csv_partial)
    summary.update(endpoint=ENDPOINT, filters={}, complete=True, pages=pages,
                   downloaded_at_utc=datetime.now(timezone.utc).isoformat())
    raw.replace(output / "8k_disclosures.jsonl")
    csv_partial.replace(output / "8k_disclosures.csv")
    (output / "8k_disclosures.summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"Saved to {output / '8k_disclosures.csv'}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        sys.exit(1)
