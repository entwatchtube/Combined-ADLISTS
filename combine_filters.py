#!/usr/bin/env python3
# combine_filters.py
import hashlib, sys
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from datetime import datetime

OUTFILE = "combined-filters.txt"
SOURCES_FILE = "sources.txt"
CUSTOM_HEADER = [
    f"! Combined filter list generated {datetime.utcnow().isoformat()}Z",
    "! Sources taken from sources.txt",
    "! --- custom rules below ---",
]

def fetch(url, timeout=30):
    req = Request(url, headers={"User-Agent": "FilterCombiner/1.0"})
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError) as e:
        print(f"Warning: failed to fetch {url}: {e}", file=sys.stderr)
        return ""

def normalize(line):
    return line.strip()

def canonical(line):
    return line

def main():
    try:
        with open(SOURCES_FILE, "r", encoding="utf-8") as f:
            sources = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    except FileNotFoundError:
        print("sources.txt not found", file=sys.stderr)
        return

    seen = set()
    out = []
    out.extend(CUSTOM_HEADER)
    out.append("")

    # optional: add your overrides here
    custom_rules = [
        "! Custom overrides go here",
    ]
    for r in custom_rules:
        r2 = normalize(r)
        if not r2:
            continue
        key = hashlib.sha1(canonical(r2).encode()).hexdigest()
        if key not in seen:
            out.append(r2)
            seen.add(key)

    for url in sources:
        text = fetch(url)
        if not text:
            continue
        for raw in text.splitlines():
            line = normalize(raw)
            if not line:
                continue
            if line.startswith("!") or line.startswith("["):
                continue
            key = hashlib.sha1(canonical(line).encode()).hexdigest()
            if key in seen:
                continue
            out.append(line)
            seen.add(key)

    data = "\n".join(out) + "\n"
    with open(OUTFILE, "w", encoding="utf-8") as f:
        f.write(data)
    print(f"Wrote {len(out)} lines to {OUTFILE}")

if __name__ == "__main__":
    main()
