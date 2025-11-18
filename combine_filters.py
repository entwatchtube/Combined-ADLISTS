#!/usr/bin/env python3
"""
combine_filters.py

- Reads sources.txt (one URL per non-empty line; lines starting with # are ignored)
- Downloads each URL in parallel with retries and backoff
- Extracts text content (handles gzip transfer automatically via requests)
- Normalizes lines, filters out blank lines and pure metadata/comments
- Deduplicates preserving first-seen order
- Writes atomically to combined-filters.txt (write to temp file then rename)
- Exits with non-zero code on fatal errors to allow GitHub Actions to surface failures
"""

from __future__ import annotations
import os
import sys
import time
import socket
import tempfile
import shutil
from pathlib import Path
from typing import List, Iterable, Set
import concurrent.futures
import threading

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:
    print("Missing dependency 'requests'. Please install with: pip install requests", file=sys.stderr)
    raise

# Config
SOURCES_FILE = Path("sources.txt")
OUTPUT_FILE = Path("combined-filters.txt")
TEMP_DIR = Path(tempfile.gettempdir())
USER_AGENT = "combine-filters/1.0 (+https://github.com/)"
MAX_WORKERS = min(8, (os.cpu_count() or 2) * 2)
REQUEST_TIMEOUT = (10, 30)  # connect timeout, read timeout in seconds
MAX_RETRIES = 3
BACKOFF_FACTOR = 1.0
VALID_STATUS = {200, 203, 206}

# Thread-safe add order-preserving set
class OrderedSet:
    def __init__(self):
        self._seen: Set[str] = set()
        self._lock = threading.Lock()
        self._items: List[str] = []

    def add(self, item: str):
        with self._lock:
            if item not in self._seen:
                self._seen.add(item)
                self._items.append(item)

    def extend(self, items: Iterable[str]):
        for it in items:
            self.add(it)

    def items(self) -> List[str]:
        return list(self._items)

def build_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["HEAD", "GET", "OPTIONS"])
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    # reduce DNS related hangs
    s.timeout = REQUEST_TIMEOUT
    return s

def read_sources(path: Path) -> List[str]:
    if not path.exists():
        print(f"Sources file not found: {path}", file=sys.stderr)
        sys.exit(2)
    urls: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # allow inline comments after url separated by whitespace
            parts = line.split()
            url = parts[0]
            if url.startswith("http://") or url.startswith("https://"):
                urls.append(url)
    return urls

def sanitize_line(line: str) -> str:
    # Normalize whitespace, strip trailing/leading spaces and control chars
    return line.rstrip("\r\n")

def filter_keep_line(line: str) -> bool:
    # Keep filter rules and whitelist rules; skip pure metadata lines that are not filter rules.
    l = line.strip()
    if not l:
        return False
    # Many lists use "!" (Adblock) or "[" sections; skip unless rule-like
    if l.startswith("!") or l.startswith("["):
        return False
    # Some lists include comments after rules; keep the whole line
    # Some lists use final blank lines or metadata lines; skip if too short
    if len(l) < 3:
        return False
    return True

def parse_text_into_lines(text: str) -> List[str]:
    out: List[str] = []
    for raw in text.splitlines():
        line = sanitize_line(raw)
        if filter_keep_line(line):
            out.append(line)
    return out

def fetch_url(session: requests.Session, url: str) -> List[str]:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code not in VALID_STATUS:
            print(f"WARN: {url} returned status {r.status_code}, skipping", file=sys.stderr)
            return []
        content_type = r.headers.get("Content-Type", "")
        # assume text content; decode using requests' encoding detection
        text = r.text
        return parse_text_into_lines(text)
    except requests.RequestException as e:
        print(f"ERROR fetching {url}: {e}", file=sys.stderr)
        return []

def combine_all(urls: List[str]) -> List[str]:
    session = build_session()
    ordered = OrderedSet()
    if not urls:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_url, session, u): u for u in urls}
        for fut in concurrent.futures.as_completed(futures):
            src = futures[fut]
            try:
                lines = fut.result()
                ordered.extend(lines)
                print(f"Fetched {len(lines)} lines from {src}")
            except Exception as e:
                print(f"ERROR processing {src}: {e}", file=sys.stderr)
    return ordered.items()

def atomic_write(path: Path, lines: List[str]):
    # write to temp file then move.
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for ln in lines:
            f.write(ln + "\n")
    # fsync to ensure durability
    try:
        fd = tmp.open("r+", encoding="utf-8")
        fd.flush()
        os.fsync(fd.fileno())
        fd.close()
    except Exception:
        pass
    tmp.replace(path)

def main():
    # Ensure predictable DNS and sockets timeouts
    socket.setdefaulttimeout(60)
    urls = read_sources(SOURCES_FILE)
    print(f"Found {len(urls)} sources")
    combined = combine_all(urls)
    print(f"Total unique rules: {len(combined)}")
    # Overwrite file atomically
    try:
        atomic_write(OUTPUT_FILE, combined)
        print(f"Wrote {OUTPUT_FILE} ({len(combined)} lines)")
    except Exception as e:
        print(f"FATAL: cannot write output: {e}", file=sys.stderr)
        sys.exit(3)

if __name__ == "__main__":
    main()
