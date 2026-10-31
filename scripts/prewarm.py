#!/usr/bin/env python3
"""Load every file of one video into every edge cache.

Usage: python3 scripts/prewarm.py VIDEO_NAME
"""
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT / "origin" / "www" / "video"
EDGES = {
    "edge1": "http://127.0.0.1:8082",
    "edge2": "http://127.0.0.1:8083",
}


def fetch(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as r:
        r.read()
        return r.headers.get("X-Cache", "?")


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"[A-Za-z0-9_-]+", sys.argv[1]):
        sys.exit("usage: python3 scripts/prewarm.py VIDEO_NAME")
    name = sys.argv[1]

    manifest = VIDEO_DIR / name / "index.m3u8"
    if not manifest.exists():
        sys.exit("no such video: " + name)

    # The manifest lists the segment files; lines starting with # are tags
    files = ["index.m3u8"] + [
        line.strip()
        for line in manifest.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    for edge, base in EDGES.items():
        counts = {}
        for fname in files:
            result = fetch(base, "/video/" + name + "/" + fname)
            counts[result] = counts.get(result, 0) + 1
        print(edge + ": " + str(len(files)) + " files -> " + str(counts))


if __name__ == "__main__":
    main()
