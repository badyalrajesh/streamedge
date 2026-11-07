#!/usr/bin/env python3
"""Varnish exporter for Kubernetes: runs next to Varnish inside the same pod.

Both containers share the /var/lib/varnish folder, so varnishstat here reads
the statistics of the Varnish container beside it. Prometheus asks this
script for the numbers every few seconds.
"""
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9100
WORKDIR = os.environ.get("VARNISH_WORKDIR", "")  # empty = Varnish default

# varnishstat name -> (prometheus name, type, help text)
METRICS = {
    "MAIN.cache_hit": ("varnish_cache_hit_total", "counter", "Cache hits"),
    "MAIN.cache_miss": ("varnish_cache_miss_total", "counter", "Cache misses"),
    "MAIN.client_req": ("varnish_client_req_total", "counter", "Requests from clients"),
    "MAIN.backend_req": ("varnish_backend_req_total", "counter", "Requests sent to the origin"),
    "MAIN.n_object": ("varnish_objects", "gauge", "Objects in cache"),
}


def read_stats():
    """Return {varnishstat name: value}, or None if varnishstat failed."""
    cmd = ["varnishstat", "-1"]
    if WORKDIR:
        cmd += ["-n", WORKDIR]
    for field in METRICS:
        cmd += ["-f", field]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    values = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in METRICS:
            values[parts[0]] = parts[1]
    return values


def build():
    values = read_stats()
    out = ["# HELP varnish_up 1 if varnishstat answered", "# TYPE varnish_up gauge",
           "varnish_up %d" % (1 if values else 0)]
    for key, (name, mtype, help_text) in METRICS.items():
        if values and key in values:
            out.append("# HELP %s %s" % (name, help_text))
            out.append("# TYPE %s %s" % (name, mtype))
            out.append("%s %s" % (name, values[key]))
    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        body = build().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep the log quiet


if __name__ == "__main__":
    print("exporter listening on port", PORT, flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
