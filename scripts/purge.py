#!/usr/bin/env python3
"""Remove one video from every edge cache.

Usage: python3 scripts/purge.py VIDEO_NAME
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EDGES = ["edge1", "edge2"]


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"[A-Za-z0-9_-]+", sys.argv[1]):
        sys.exit("usage: python3 scripts/purge.py VIDEO_NAME")
    name = sys.argv[1]

    for edge in EDGES:
        # A "ban" tells Varnish: forget every cached object whose URL matches
        cmd = ["docker", "compose", "exec", "-T", edge,
               "varnishadm", "ban", "req.url", "~", "^/video/" + name + "/"]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            status = "ok"
        else:
            status = "FAILED: " + (r.stderr or r.stdout).strip()
        print(edge + ": purge /video/" + name + "/ -> " + status)


if __name__ == "__main__":
    main()
