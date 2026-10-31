# StreamEdge - Level 5: Monitoring with Prometheus and Grafana

Until now you checked the system by hand with `curl` and `varnishstat`.
Level 5 adds a live dashboard, so you can watch cache hits, traffic and
failures change as they happen.

```
 edge1, edge2 --(varnishstat)--> exporter.py (port 9100) --+
                                                           +--> Prometheus --> Grafana
 HAProxy --------(/metrics on port 8404)-------------------+     (port 9090)   (port 3000)
```

- **Prometheus** asks each source for its numbers every 5 seconds and stores
  them as a history.
- **Grafana** draws that history as graphs. Your dashboard is loaded from a
  file, so it is saved in git with the rest of the project.
- **exporter.py** is a small Python script you write yourself. It reads the
  Varnish numbers and hands them to Prometheus.

Files added or changed in this level:

```
streamedge/
  docker-compose.yml          CHANGED: adds prometheus and grafana
  lb/haproxy.cfg              CHANGED: one line adds a /metrics endpoint
  scripts/exporter.py         NEW: Varnish numbers for Prometheus
  monitoring/
    prometheus.yml            NEW: what to collect and how often
    grafana/
      provisioning/
        datasources/datasource.yml   NEW: connects Grafana to Prometheus
        dashboards/dashboards.yml    NEW: tells Grafana where dashboards are
      dashboards/streamedge.json     NEW: the dashboard itself
```

What the dashboard shows:
- Edge status (UP or DOWN) from HAProxy's health checks
- Overall cache hit ratio, and hit ratio per edge over time
- Requests per second per edge
- Requests that reached the origin (the load the edges did NOT absorb)
- HAProxy responses by status code, and average response time
- Objects stored in cache

Two small notes about the numbers:
- Each edge shows about 0.5 requests per second even when idle. That is
  HAProxy's health check (`/healthz`, every 2 seconds).
- Grafana's login is admin / admin. That is fine for a lab on your own
  laptop, but see the VirtualBox note in Step 8.

---

# Steps (Ubuntu Server on VirtualBox)

Before you start, Level 4 must be working.

## Step 1: Save and tag Level 4 (skip if already done)

```bash
cd ~/streamedge
git add .
git commit -m "Level 4: auto ingest watcher, purge and pre-warm scripts"
git tag level-4
git push
git push origin level-4
```

If git says "nothing to commit", run the `git tag` and `git push` lines only.

## Step 2: Ignore the exporter log

```bash
cd ~/streamedge
echo "exporter.log" >> .gitignore
```

## Step 3: Make HAProxy publish its numbers

This adds one line inside the `frontend stats` section of lb/haproxy.cfg.
Run it **once**:

```bash
sed -i '/bind \*:8404/a http-request use-service prometheus-exporter if { path /metrics }' lb/haproxy.cfg
grep -n -B1 -A1 prometheus lb/haproxy.cfg
docker compose restart lb
```

The `grep` should show the new line right under `bind *:8404`. Then check
the numbers are published:

```bash
curl -s http://localhost:8404/metrics | grep haproxy_server_up
```

You should see lines for edge1 and edge2 ending in 1 (1 means UP).

## Step 4: Create and start the exporter

```bash
cat > scripts/exporter.py << 'EOF'
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
EOF
```

Start it in the background and test it:

```bash
nohup python3 scripts/exporter.py > exporter.log 2>&1 &
sleep 2
curl -s http://localhost:9100/metrics | head -20
```

You should see `varnish_up{edge="edge1"} 1` and similar lines. If a value
is 0, that edge is not running: check `docker compose ps`.

## Step 5: Create the monitoring files

```bash
mkdir -p monitoring/grafana/provisioning/datasources
mkdir -p monitoring/grafana/provisioning/dashboards
mkdir -p monitoring/grafana/dashboards
```

Prometheus config:

```bash
cat > monitoring/prometheus.yml << 'EOF'
global:
  scrape_interval: 5s
  scrape_timeout: 4s

scrape_configs:
  - job_name: haproxy
    static_configs:
      - targets: ["lb:8404"]

  - job_name: varnish
    static_configs:
      - targets: ["host.docker.internal:9100"]
EOF
```

Grafana data source (how Grafana finds Prometheus):

```bash
cat > monitoring/grafana/provisioning/datasources/datasource.yml << 'EOF'
apiVersion: 1
datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
EOF
```

Grafana dashboard loader (where to find dashboard files):

```bash
cat > monitoring/grafana/provisioning/dashboards/dashboards.yml << 'EOF'
apiVersion: 1
providers:
  - name: streamedge
    type: file
    allowUiUpdates: true
    options:
      path: /etc/grafana/dashboards
EOF
```

The dashboard (long, but just paste it as one block):

```bash
cat > monitoring/grafana/dashboards/streamedge.json << 'EOF'
{
 "uid": "streamedge",
 "title": "StreamEdge",
 "schemaVersion": 39,
 "version": 1,
 "editable": true,
 "refresh": "5s",
 "time": {
  "from": "now-15m",
  "to": "now"
 },
 "panels": [
  {
   "id": 1,
   "type": "stat",
   "title": "Edge status (from HAProxy)",
   "datasource": {
    "type": "prometheus",
    "uid": "prometheus"
   },
   "gridPos": {
    "x": 0,
    "y": 0,
    "w": 8,
    "h": 4
   },
   "options": {
    "reduceOptions": {
     "calcs": [
      "lastNotNull"
     ],
     "fields": "",
     "values": false
    },
    "colorMode": "background",
    "textMode": "auto"
   },
   "fieldConfig": {
    "defaults": {
     "unit": "short",
     "mappings": [
      {
       "type": "value",
       "options": {
        "0": {
         "text": "DOWN",
         "color": "red"
        },
        "1": {
         "text": "UP",
         "color": "green"
        }
       }
      }
     ],
     "thresholds": {
      "mode": "absolute",
      "steps": [
       {
        "color": "red",
        "value": null
       },
       {
        "color": "green",
        "value": 1
       }
      ]
     }
    },
    "overrides": []
   },
   "targets": [
    {
     "refId": "A",
     "expr": "haproxy_server_up{proxy=\"edges\"}",
     "legendFormat": "{{server}}",
     "datasource": {
      "type": "prometheus",
      "uid": "prometheus"
     }
    }
   ]
  },
  {
   "id": 2,
   "type": "stat",
   "title": "Overall cache hit ratio (last 5 min)",
   "datasource": {
    "type": "prometheus",
    "uid": "prometheus"
   },
   "gridPos": {
    "x": 8,
    "y": 0,
    "w": 8,
    "h": 4
   },
   "options": {
    "reduceOptions": {
     "calcs": [
      "lastNotNull"
     ],
     "fields": "",
     "values": false
    },
    "colorMode": "background",
    "textMode": "auto"
   },
   "fieldConfig": {
    "defaults": {
     "unit": "percentunit",
     "mappings": [],
     "thresholds": {
      "mode": "absolute",
      "steps": [
       {
        "color": "red",
        "value": null
       },
       {
        "color": "yellow",
        "value": 0.5
       },
       {
        "color": "green",
        "value": 0.8
       }
      ]
     }
    },
    "overrides": []
   },
   "targets": [
    {
     "refId": "A",
     "expr": "sum(increase(varnish_cache_hit_total[5m])) / (sum(increase(varnish_cache_hit_total[5m])) + sum(increase(varnish_cache_miss_total[5m])))",
     "legendFormat": "hit ratio",
     "datasource": {
      "type": "prometheus",
      "uid": "prometheus"
     }
    }
   ]
  },
  {
   "id": 3,
   "type": "stat",
   "title": "Objects in cache",
   "datasource": {
    "type": "prometheus",
    "uid": "prometheus"
   },
   "gridPos": {
    "x": 16,
    "y": 0,
    "w": 8,
    "h": 4
   },
   "options": {
    "reduceOptions": {
     "calcs": [
      "lastNotNull"
     ],
     "fields": "",
     "values": false
    },
    "colorMode": "background",
    "textMode": "auto"
   },
   "fieldConfig": {
    "defaults": {
     "unit": "short",
     "mappings": []
    },
    "overrides": []
   },
   "targets": [
    {
     "refId": "A",
     "expr": "sum(varnish_objects)",
     "legendFormat": "objects",
     "datasource": {
      "type": "prometheus",
      "uid": "prometheus"
     }
    }
   ]
  },
  {
   "id": 4,
   "type": "timeseries",
   "title": "Cache hit ratio per edge",
   "datasource": {
    "type": "prometheus",
    "uid": "prometheus"
   },
   "gridPos": {
    "x": 0,
    "y": 4,
    "w": 12,
    "h": 8
   },
   "fieldConfig": {
    "defaults": {
     "unit": "percentunit",
     "min": 0,
     "max": 1
    },
    "overrides": []
   },
   "targets": [
    {
     "refId": "A",
     "expr": "rate(varnish_cache_hit_total[1m]) / (rate(varnish_cache_hit_total[1m]) + rate(varnish_cache_miss_total[1m]))",
     "legendFormat": "{{edge}}",
     "datasource": {
      "type": "prometheus",
      "uid": "prometheus"
     }
    }
   ]
  },
  {
   "id": 5,
   "type": "timeseries",
   "title": "Requests per second per edge",
   "datasource": {
    "type": "prometheus",
    "uid": "prometheus"
   },
   "gridPos": {
    "x": 12,
    "y": 4,
    "w": 12,
    "h": 8
   },
   "fieldConfig": {
    "defaults": {
     "unit": "reqps"
    },
    "overrides": []
   },
   "targets": [
    {
     "refId": "A",
     "expr": "rate(varnish_client_req_total[1m])",
     "legendFormat": "{{edge}}",
     "datasource": {
      "type": "prometheus",
      "uid": "prometheus"
     }
    }
   ]
  },
  {
   "id": 6,
   "type": "timeseries",
   "title": "Requests that reached the origin (per second)",
   "datasource": {
    "type": "prometheus",
    "uid": "prometheus"
   },
   "gridPos": {
    "x": 0,
    "y": 12,
    "w": 12,
    "h": 8
   },
   "fieldConfig": {
    "defaults": {
     "unit": "reqps"
    },
    "overrides": []
   },
   "targets": [
    {
     "refId": "A",
     "expr": "sum(rate(varnish_backend_req_total[1m]))",
     "legendFormat": "origin",
     "datasource": {
      "type": "prometheus",
      "uid": "prometheus"
     }
    }
   ]
  },
  {
   "id": 7,
   "type": "timeseries",
   "title": "HAProxy responses per second by status",
   "datasource": {
    "type": "prometheus",
    "uid": "prometheus"
   },
   "gridPos": {
    "x": 12,
    "y": 12,
    "w": 12,
    "h": 8
   },
   "fieldConfig": {
    "defaults": {
     "unit": "reqps"
    },
    "overrides": []
   },
   "targets": [
    {
     "refId": "A",
     "expr": "sum by (code) (rate(haproxy_backend_http_responses_total{proxy=\"edges\"}[1m]))",
     "legendFormat": "{{code}}",
     "datasource": {
      "type": "prometheus",
      "uid": "prometheus"
     }
    }
   ]
  },
  {
   "id": 8,
   "type": "timeseries",
   "title": "Average response time (HAProxy to edges)",
   "datasource": {
    "type": "prometheus",
    "uid": "prometheus"
   },
   "gridPos": {
    "x": 0,
    "y": 20,
    "w": 12,
    "h": 8
   },
   "fieldConfig": {
    "defaults": {
     "unit": "s"
    },
    "overrides": []
   },
   "targets": [
    {
     "refId": "A",
     "expr": "haproxy_backend_response_time_average_seconds{proxy=\"edges\"}",
     "legendFormat": "response time",
     "datasource": {
      "type": "prometheus",
      "uid": "prometheus"
     }
    }
   ]
  }
 ]
}
EOF
```

Check all four files exist:

```bash
find monitoring -type f
```

## Step 6: Replace docker-compose.yml

This keeps all your services exactly as they were and adds Prometheus and
Grafana, plus two small storage volumes so your history survives restarts.

```bash
cat > docker-compose.yml << 'EOF'
services:
  origin:
    image: nginx:1.27-alpine
    ports:
      - "8081:80"
    volumes:
      - ./origin/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./origin/www:/usr/share/nginx/html:ro

  edge1:
    image: varnish:7.5
    ports:
      - "127.0.0.1:8082:80"
    volumes:
      - ./edge/default.vcl:/etc/varnish/default.vcl:ro
    depends_on:
      - origin

  edge2:
    image: varnish:7.5
    ports:
      - "127.0.0.1:8083:80"
    volumes:
      - ./edge/default.vcl:/etc/varnish/default.vcl:ro
    depends_on:
      - origin

  lb:
    image: haproxy:2.8
    ports:
      - "8080:8080"
      - "8404:8404"
    volumes:
      - ./lb/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro
    depends_on:
      - edge1
      - edge2

  prometheus:
    image: prom/prometheus:v2.53.0
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prom_data:/prometheus
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      - lb

  grafana:
    image: grafana/grafana:11.1.0
    ports:
      - "3000:3000"
    volumes:
      - ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./monitoring/grafana/dashboards:/etc/grafana/dashboards:ro
      - grafana_data:/var/lib/grafana
    depends_on:
      - prometheus

volumes:
  prom_data:
  grafana_data:
EOF
```

Start the new services:

```bash
docker compose up -d
docker compose ps
```

You should see 6 containers running: origin, edge1, edge2, lb, prometheus
and grafana. The first start downloads two images, so it can take a minute.

## Step 7: Check that Prometheus can reach both sources

```bash
curl -s http://localhost:9090/api/v1/targets | grep -o '"health":"[a-z]*"'
```

You should see `"health":"up"` twice (one for haproxy, one for varnish).
If one says `down`, see "If something goes wrong" at the bottom.

## Step 8: Open Grafana from Windows

Add port-forward rules in VirtualBox (Settings, Network, Adapter 1,
Advanced, Port Forwarding):

- TCP, **Host IP 127.0.0.1**, Host Port 3000, Guest Port 3000
- Optional, to open Prometheus itself: TCP, Host IP 127.0.0.1,
  Host Port 9090, Guest Port 9090

Setting Host IP to 127.0.0.1 means only your own Windows PC can open them,
not other computers on your network. Do this for these two ports because
Grafana uses a default password.

Open http://localhost:3000 in your Windows browser and log in with
`admin` / `admin`. If it asks you to change the password, you can click Skip
for this lab.

Open the menu, then Dashboards, then **StreamEdge**. The graphs are mostly
empty because there is no traffic yet. Next, you make some.

---

# Experiments

Keep the Grafana dashboard open in your Windows browser (it refreshes every
5 seconds). Set the time range at the top right to "Last 15 minutes".

## Experiment 1: Make steady traffic and watch it appear

In Ubuntu, start a traffic loop. It requests random segments of the
`sample` video about 5 times per second:

```bash
while true; do curl -s -o /dev/null http://localhost:8080/video/sample/seg_00$((RANDOM%3)).ts; sleep 0.2; done
```

(Leave it running. Press Ctrl+C to stop it. If your sample video has fewer
than 5 segments, some requests will return 404. That shows up as a 4xx line
in the HAProxy graph, which is also a useful thing to see.)

Within a minute you should see:
- Requests per second go up on both edges
- Cache hit ratio climb toward 100% (the first request for each file is a
  miss, then everything is a hit)
- Requests reaching the origin drop to almost nothing after the start

This is the CDN idea in one picture: lots of viewer traffic, very little
origin traffic.

## Experiment 2: Purge and watch the dip

With the traffic loop still running (use a second terminal, or stop the loop,
run the command, and restart the loop):

```bash
python3 scripts/purge.py sample
```

Watch the dashboard. The hit ratio dips, requests reaching the origin spike
briefly (the edges refill from the origin), then everything recovers. This
is what a purge costs.

## Experiment 3: Kill an edge

```bash
docker compose stop edge1
```

In about 5 seconds, the Edge status panel shows edge1 as DOWN (red). The
requests graph for edge1 drops to nothing and edge2 takes all the traffic.
Viewers notice nothing. Now bring it back:

```bash
docker compose start edge1
```

Edge1 turns green again, but its hit ratio starts low because its cache
came back empty. You can watch it warm up again.

## Experiment 4: Compare pre-warming with not pre-warming

Drop a new video into `incoming/` (see Level 4, Experiment 1, with a new
name like `movie3`). Then, with the traffic loop stopped, do this twice, on
two different videos:

- Video A: play it straight away in the browser. Watch "requests that
  reached the origin" jump.
- Video B: run `python3 scripts/prewarm.py NAME` first, wait 10 seconds,
  then play it. Compare how much the origin graph jumps this time.

## Experiment 5 (optional): Ask Prometheus yourself

Open http://localhost:9090 in your Windows browser (if you added the rule)
and type this in the query box, then press Execute:

```
sum(rate(varnish_client_req_total[1m]))
```

Every Grafana panel is just a query like this one. Try the query from the
"hit ratio" panel by opening its edit menu in Grafana to see how it is
written.

---

# Save Level 5 to GitHub

```bash
cd ~/streamedge
git add .
git commit -m "Level 5: Prometheus and Grafana monitoring dashboard"
git tag level-5
git push
git push origin level-5
git ls-files
```

`git ls-files` should include the monitoring folder and exporter.py, and
should not include `exporter.log`.

Update the level list at the top of README.md:

```
Levels
- level-1: NGINX origin + HLS video
- level-2: Varnish edge cache
- level-3: two edges + HAProxy failover
- level-4: auto ingest, purge, pre-warm
- level-5: Prometheus + Grafana monitoring
```

Take a screenshot of your dashboard during Experiment 3 or 4. It is a good
thing to put in the repo's README and to show in an interview.

---

# After you restart the Ubuntu VM

Docker containers restart on their own only if you tell them to; the two
Python scripts never do. After a reboot, run:

```bash
cd ~/streamedge
docker compose up -d
nohup python3 scripts/watcher.py > watcher.log 2>&1 &
nohup python3 scripts/exporter.py > exporter.log 2>&1 &
```

To stop the scripts: `pkill -f watcher.py` and `pkill -f exporter.py`

---

# If something goes wrong

- A Prometheus target is `down` (Step 7):
  - **haproxy down:** run `curl -s http://localhost:8404/metrics | head`.
    If that fails, repeat Step 3 and run `docker compose logs lb`.
  - **varnish down:** check the exporter runs with `pgrep -f exporter.py`
    and `curl -s http://localhost:9100/metrics | head`. If that works but
    Prometheus still cannot connect, Ubuntu's firewall may be blocking
    Docker: run `sudo ufw status`. If it says active, run
    `sudo ufw allow 9100/tcp` and check again.
- The command in Step 7 shows nothing: Prometheus is still starting. Wait
  10 seconds and run it again, or check `docker compose logs prometheus`.
- Grafana loads but there is no StreamEdge dashboard: run
  `docker compose logs grafana | tail -30` and look for a provisioning
  error, usually a typo in one of the four files from Step 5.
- Panels say "No data": make sure the traffic loop is running, set the time
  range to "Last 15 minutes", and wait about 30 seconds.
- Hit ratio panels have gaps: that is normal when there is no traffic (the
  calculation divides by zero).
- `sed` added the HAProxy line twice: delete the duplicate with
  `nano lb/haproxy.cfg`, then run `docker compose restart lb`.
- Port 3000 or 9090 is busy: stop whatever uses it, or change the left
  number in the `ports` line of docker-compose.yml.
- Image fails to pull: tags can change. Change the tag to `latest` (for
  example `grafana/grafana:latest`) and run `docker compose up -d` again.

# Next: Level 6

Break it on purpose. Stop the origin, flood one video, kill both edges, and
use your new dashboard to see what happens. Then write a one-page postmortem
of what failed, how you noticed, and how you fixed it. That is the story you
tell in an interview.

