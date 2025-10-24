#!/usr/bin/env python3
# combine_filters.py
import hashlib
import re
import sys
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

SOURCES_FILE = "sources.txt"
OUTFILE = "combined-filters.txt"

HEAD = [
    f"! Combined filter list generated {datetime.utcnow().isoformat()}Z",
    "! Sources listed in sources.txt",
    "! Normalization: lowercased, collapsed whitespace, hosts -> ||domain^ conversion where applicable",
    "! --- custom rules below ---",
    "",
]

# Regexes for hosts-style lines: "0.0.0.0 example.com" or ":: example.com"
_RX_HOSTS = re.compile(r'^(?:0\.0\.0\.0|127\.0\.0\.1|::1|\:|::)\s+([^\s#]+)', re.IGNORECASE)

def fetch(url, timeout=30):
    req = Request(url, headers={"User-Agent": "FilterCombiner/1.0"})
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError) as e:
        print(f"Warning: failed to fetch {url}: {e}", file=sys.stderr)
        return ""

def normalize(line):
    # strip comments that start with '!' or '#', but keep inline # for CSS selectors
    line = line.strip()
    if not line:
        return ""
    # Full-line comments for adblock lists often start with '!' or '['
    if line.startswith("!") or line.startswith("["):
        return ""
    # Remove inline comments for hosts format (# ...)
    if line.startswith("#"):
        return ""
    # Handle hosts-style lines: convert to adblock network rule
    m = _RX_HOSTS.match(line)
    if m:
        domain = m.group(1).strip().lower()
        # drop trailing dot
        domain = domain.rstrip('.')
        if domain:
            return f"||{domain}^"
        return ""
    # For plain lines that look like IP or host entries with domain second
    parts = line.split()
    if len(parts) >= 2 and (parts[0].count('.') >= 1 and re.match(r'^[A-Za-z0-9.-]+$', parts[1])):
        # probable hosts-like: convert
        domain = parts[1].strip().lower().rstrip('.')
        return f"||{domain}^"

    # collapse multiple spaces and lower-case
    line = re.sub(r'\s+', ' ', line).lower()

    # normalize certain common adblock options where order is irrelevant:
    # split off options after $ and sort options set (basic best-effort)
    if '$' in line and line.count('$') == 1:
        main, opts = line.split('$', 1)
        opts = opts.strip()
        # options like third-party,script,domain=...
        opts_parts = [o.strip() for o in opts.split(',') if o.strip()]
        # sort to stabilize order (note: this is a heuristic)
        opts_parts_sorted = sorted(opts_parts)
        line = main.strip() + '$' + ','.join(opts_parts_sorted)

    return line.strip()

def canonical_key(line):
    # produce a stable key for deduplication
    # use SHA1 of normalized line
    return hashlib.sha1(line.encode('utf-8')).hexdigest()

def main():
    try:
        with open(SOURCES_FILE, 'r', encoding='utf-8') as f:
            sources = [l.strip() for l in f if l.strip() and not l.strip().startswith('#')]
    except FileNotFoundError:
        print("sources.txt not found", file=sys.stderr)
        return

    seen = set()
    out_lines = []
    out_lines.extend(HEAD)

    # optional custom overrides (kept first)
    custom_overrides = [
        # add lines you want to force first, e.g. allow rules or site-specific exceptions
        # "@@||example.com^$document"
    ]
    for r in custom_overrides:
        r2 = normalize(r)
        if not r2:
            continue
        key = canonical_key(r2)
        if key not in seen:
            out_lines.append(r2)
            seen.add(key)

    for url in sources:
        print(f"Fetching {url}", file=sys.stderr)
        text = fetch(url)
        if not text:
            continue
        for raw in text.splitlines():
            n = normalize(raw)
            if not n:
                continue
            key = canonical_key(n)
            if key in seen:
                continue
            seen.add(key)
            out_lines.append(n)

    data = "\n".join(out_lines) + "\n"
    try:
        with open(OUTFILE, 'w', encoding='utf-8') as f:
            f.write(data)
    except Exception as e:
        print(f"Error writing {OUTFILE}: {e}", file=sys.stderr)
        return

    print(f"Wrote {len(out_lines)} lines to {OUTFILE}", file=sys.stderr)

if __name__ == '__main__':
    main()
