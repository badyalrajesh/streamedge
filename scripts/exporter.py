#!/usr/bin/env python3
"""Tiny Prometheus exporter: reads Varnish stats from each edge container.

Prometheus asks this script for numbers every few seconds. The script runs
varnishstat inside each edge and prints the numbers in Prometheus format.
"""
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EDGES = ["edge1", "edge2"]
PORT = 9100

# varnishstat name -> (prometheus name, type, help text)
METRICS = {
    "MAIN.cache_hit": ("varnish_cache_hit_total", "counter", "Cache hits"),
    "MAIN.cache_miss": ("varnish_cache_miss_total", "counter", "Cache misses"),
    "MAIN.client_req": ("varnish_client_req_total", "counter", "Requests from clients"),
    "MAIN.backend_req": ("varnish_backend_req_total", "counter", "Requests sent to the origin"),
    "MAIN.n_object": ("varnish_objects", "gauge", "Objects in cache"),
}


def read_edge(edge):
    """Return {varnishstat name: value} for one edge, or None if it is down."""
    cmd = ["docker", "compose", "exec", "-T", edge, "varnishstat", "-1"]
    for field in METRICS:
        cmd += ["-f", field]
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=3)
    except subprocess.TimeoutExpired:
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
    data = {edge: read_edge(edge) for edge in EDGES}
    out = ["# HELP varnish_up 1 if the edge answered", "# TYPE varnish_up gauge"]
    for edge, values in data.items():
        out.append('varnish_up{edge="%s"} %d' % (edge, 1 if values else 0))
    for key, (name, mtype, help_text) in METRICS.items():
        out.append("# HELP %s %s" % (name, help_text))
        out.append("# TYPE %s %s" % (name, mtype))
        for edge, values in data.items():
            if values and key in values:
                out.append('%s{edge="%s"} %s' % (name, edge, values[key]))
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
