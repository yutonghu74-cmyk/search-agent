"""Fetch an attributable Commons image fixture for local cleaning/retrieval tests.

Stdlib-only (urllib + threading), so it runs on a bare Python 3.11+ install. It
crawls a fixed set of distinct content types (10 animal/person categories) via
the Wikimedia Commons API, keeps only freely reusable files (CC0 / CC BY /
Public domain, JPEG/PNG, <= 5 MB), dedups by SHA-256 and page id, and writes a
``test-media-manifest@1`` JSON plus the image files under ``var/`` (gitignored).

Usage::

    python3 tools/fetch_commons_test_media.py                    # 10 labels x 50
    python3 tools/fetch_commons_test_media.py --per-label 10     # 10 labels x 10
    python3 tools/fetch_commons_test_media.py --per-label 25 --dest var/test-media-commons-250
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
API = "https://commons.wikimedia.org/w/api.php"
QUERIES = {
    "dog": "dog",
    "cat": "cat",
    "fox": "fox",
    "parrot": "parrot",
    "tiger": "tiger",
    "horse": "horse",
    "rabbit": "rabbit",
    "elephant": "elephant",
    "deer": "deer",
    "dog_person": "person with dog",
}
TITLE_TERMS = {
    "dog": ("dog", "canine"), "cat": ("cat", "feline"),
    "fox": ("fox", "vulpes"), "parrot": ("parrot", "macaw", "cockatoo"),
    "tiger": ("tiger",), "horse": ("horse",),
    "rabbit": ("rabbit", "hare"), "elephant": ("elephant",),
    "deer": ("deer", "stag"), "dog_person": ("dog", "canine"),
}
ALLOWED = ("CC0", "CC BY", "Public domain")
FILE_MAX_BYTES = 5_000_000
HEADERS = {"User-Agent": "DSHLocalFixture/1.0 (local research dataset; contact: https://commons.wikimedia.org/)"}


class StripTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)


def plain(value):
    parser = StripTags()
    parser.feed(value or "")
    return unescape("".join(parser.parts)).strip()[:500]


def get(url, limit):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=25) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("file exceeds limit")
        return data


def _search_page(query, offset):
    params = {
        "action": "query", "generator": "search", "gsrsearch": query,
        "gsrnamespace": "6", "gsrlimit": "50", "gsroffset": offset,
        "prop": "imageinfo", "iiprop": "url|extmetadata|mime", "iiurlwidth": "640",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|UsageTerms|Credit",
        "format": "json",
    }
    payload = json.loads(get(API + "?" + urllib.parse.urlencode(params), 8_000_000))
    return sorted(payload.get("query", {}).get("pages", {}).values(),
                  key=lambda row: row.get("index", 999))


def api_candidates(label, want):
    """Fetch up to ``want`` usable metadata candidates, paging the search API."""
    found, offset, seen = [], 0, set()
    for _ in range(20):
        page = _search_page(QUERIES[label], offset)
        if not page:
            break
        advanced = False
        for row in page:
            page_id = row.get("pageid")
            if page_id is None or page_id in seen:
                continue
            seen.add(page_id)
            advanced = True
            item = candidate(row, label)
            if item is not None:
                found.append(item)
                if len(found) >= want:
                    return found
        if not advanced:
            break
        offset += 50
    return found


def candidate(page, label):
    info = (page.get("imageinfo") or [{}])[0]
    metadata = info.get("extmetadata") or {}
    meta = lambda key: metadata.get(key, {}).get("value", "")
    license_name = plain(meta("LicenseShortName"))
    title = page.get("title", "")
    url = info.get("thumburl") or ""
    if (not license_name.startswith(ALLOWED) or not title.startswith("File:")
            or not re.search(r"\.(?:jpe?g|png)$", title, re.I)
            or not any(term in title.casefold() for term in TITLE_TERMS[label])
            or not url.startswith("https://")
            or not re.search(r"\.(?:jpe?g|png)(?:\?|$)", url, re.I)):
        return None
    return {
        "source_asset_id": str(page["pageid"]),
        "source_title": title, "source_url": "https://commons.wikimedia.org/wiki/" +
                      urllib.parse.quote(title.replace(" ", "_")),
        "download_url": url, "license": license_name,
        "license_url": plain(meta("LicenseUrl")),
        "artist": plain(meta("Artist")), "credit": plain(meta("Credit")),
        "usage_terms": plain(meta("UsageTerms")),
    }


def fetch(item):
    try:
        data = get(item["download_url"], FILE_MAX_BYTES)
        if data.startswith(b"\xff\xd8\xff"):
            ext = "jpg"
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            ext = "png"
        else:
            return None
        return item, data, ext
    except (OSError, ValueError):
        return None


def load_manifest(dest):
    manifest_path = Path(dest) / "manifest.json"
    if not manifest_path.exists():
        return manifest_path, []
    records = json.loads(manifest_path.read_text())["items"]
    return manifest_path, [row for row in records if row["label_search_intent"] in QUERIES]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", default=str(ROOT / "var" / "test-media-commons-500"),
                        help="output directory (default var/test-media-commons-500)")
    parser.add_argument("--per-label", type=int, default=50,
                        help="images to download per content type (default 50)")
    parser.add_argument("--labels", default=",".join(QUERIES),
                        help="comma-separated QUERIES keys to crawl (default: all)")
    parser.add_argument("--workers", type=int, default=5,
                        help="concurrent download threads (default 5)")
    args = parser.parse_args(argv)
    labels = [label.strip() for label in args.labels.split(",") if label.strip()]
    if any(label not in QUERIES for label in labels):
        parser.error("labels must be one of: " + ", ".join(QUERIES))
    if not 1 <= args.per_label <= 400:
        parser.error("--per-label must be between 1 and 400")

    dest = Path(args.dest)
    (dest / "images").mkdir(parents=True, exist_ok=True)
    manifest_path, records = load_manifest(dest)
    digests = {row["content_digest"].removeprefix("sha256:") for row in records}
    seen_pages = {row["source_asset_id"] for row in records}
    by_label = {label: sum(row["label_search_intent"] == label for row in records) for label in labels}

    # Fetch several times the still-needed count so download failures do not
    # strand a label below its target.
    pools = {}
    for label in labels:
        need = args.per_label - by_label[label]
        if need <= 0:
            pools[label] = []
            continue
        pools[label] = [item for item in api_candidates(label, need * 3)
                        if item["source_asset_id"] not in dict.fromkeys(seen_pages)]
        print(label, "metadata candidates", len(pools[label]), flush=True)

    chosen = []
    for label in labels:
        for item in pools.get(label, []):
            if item["source_asset_id"] in seen_pages:
                continue
            seen_pages.add(item["source_asset_id"])
            chosen.append((label, item))
        print(label, "queued for download", sum(1 for l, _ in chosen if l == label), flush=True)

    downloaded = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(fetch, item): (label, item) for label, item in chosen}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                item, data, ext = result
                downloaded[item["source_asset_id"]] = (item, data, ext)

    space = lambda label: args.per_label - by_label[label]
    for label in labels:
        for _, item in chosen:
            if space(label) <= 0:
                break
            result = downloaded.get(item["source_asset_id"])
            if result is None:
                continue
            _, data, ext = result
            sha = hashlib.sha256(data).hexdigest()
            if sha in digests:
                continue
            digests.add(sha)
            filename = f"{label}-{by_label[label] + 1:03d}.{ext}"
            (dest / "images" / filename).write_bytes(data)
            records.append({
                "media_unit_id": "commons-" + item["source_asset_id"],
                "asset_version": "thumb-640-v1", "source_namespace": "wikimedia-commons",
                "source_asset_id": item["source_asset_id"],
                "file": "images/" + filename, "content_digest": "sha256:" + sha,
                "label_search_intent": label, "source_url": item["source_url"],
                "source_title": item["source_title"], "license": item["license"],
                "license_url": item["license_url"], "artist": item["artist"],
                "credit": item["credit"], "usage_terms": item["usage_terms"],
                "usage_note": "Local development test; preserve attribution and license terms",
            })
            by_label[label] += 1
        print(label, "downloaded", by_label[label], "/", args.per_label, flush=True)

    manifest = {"schema": "test-media-manifest@1", "source": "Wikimedia Commons",
                "items": records}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("total", len(records), "directory", dest, flush=True)


if __name__ == "__main__":
    main()