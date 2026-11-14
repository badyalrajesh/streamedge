# StreamEdge - Level 9: Kubernetes with k3s

Until now, Docker Compose or Ansible ran StreamEdge on one machine. Level 9
runs the same system as a **Kubernetes cluster**. Kubernetes keeps the
number of pods you asked for, restarts anything that fails, replaces pods
during updates, and lets you scale an edge with one command.

You will use **k3s**, a light but real Kubernetes that installs with one
command on your Ubuntu VM.

```
The pipeline roadmap:

  Level 6   Docker images
  Level 7   CI with GitHub Actions
  Level 8   Ansible
  Level 9   Kubernetes (k3s)         <-- you are here
  Level 10  GitOps with Argo CD      the cluster updates itself from git
  Level 11  Break it on purpose      failure experiments + postmortem
```

What runs in the cluster:

```
                    +-- edge pod --+
 Windows browser    |  varnish     |
 --> lb (HAProxy) --+  exporter    +--> origin (NGINX) --> video volume
     port 8080      +-- edge pod --+                           ^
                    (2 replicas)                               |
                                                    watcher (converts uploads)

 Prometheus --> finds the edge pods through DNS, scrapes each exporter
 Grafana    --> draws the dashboard from Prometheus
```

From Docker Compose to Kubernetes:

| In Compose | In Kubernetes |
|---|---|
| A service | A **Deployment** (keeps N pods running) plus a **Service** (a stable address) |
| `edge1` and `edge2` | One Deployment with `replicas: 2` |
| `ports:` | A Service of type LoadBalancer |
| Config file mounted from the VM | A **ConfigMap** |
| Data folder (videos) | A **PersistentVolumeClaim** |
| `restart: unless-stopped` | The Deployment replaces failed pods by itself |
| `depends_on` | Readiness probes: a pod gets traffic only when it is ready |
| Exporter on the host | A **sidecar** container inside every edge pod |

Words you will meet:
- **Pod:** one or more containers running together. The smallest unit.
- **Deployment:** "keep this many pods of this kind running, and update them
  safely".
- **Service:** a stable name and address in front of changing pods.
- **Headless service:** a Service whose DNS name returns every pod's own
  address. HAProxy and Prometheus use it to find the edges.
- **ConfigMap:** configuration stored in the cluster and given to pods.
- **PVC:** a request for storage that outlives pods.
- **Probe:** a health check Kubernetes runs on a container.
- **Kustomize:** builds all the YAML plus ConfigMaps from your files. It is
  built into `kubectl` (`kubectl apply -k`).

This level starts with a cleanup (Step 0) because the old Docker setup and
Kubernetes cannot share the VM. After it you only have the git repository.

What changes in your project:

```
streamedge/
  exporter/                  NEW: the per-pod Varnish exporter image
    exporter.py
    Dockerfile
  k8s/                       NEW: everything Kubernetes
    kustomization.yaml       lists files, config maps, and image versions
    namespace.yaml, origin.yaml, edge.yaml, lb.yaml,
    watcher.yaml, prometheus.yaml, grafana.yaml
    config/                  haproxy.cfg, prometheus.yml, dashboard, ...
  .github/workflows/ci.yml   CHANGED: also builds the exporter image
```

Two things are different from the Docker setup:
- **HAProxy finds the edges through DNS** instead of fixed names, so you can
  scale the edges without editing anything. It also hashes by URL with
  "consistent hashing", so adding an edge moves only some of the files.
- **The exporter is now a container** next to each Varnish. Both share a
  folder, so the exporter can read Varnish's statistics. This is why Level 9
  adds one more image.

Your VM needs enough memory: this level wants about 2 GB free for k3s and the
pods. Check with `free -h` (the "available" column). Your 4 GB VM is enough
once the cleanup in Step 0 is done.

---

# Steps (Ubuntu Server on VirtualBox)

Before you start, Level 8 should have worked at least once. You have proved
the playbook works, which is all you need.

## Step 0: Clean up the old setup (required)

Do this before anything else in Level 9. It is not optional housekeeping:
Kubernetes cannot start properly while the old Docker setup is still there.

**Why it is required**

| Problem if you skip it | Why it happens |
|---|---|
| Kubernetes cannot open its ports | The Docker containers already hold 8080, 8404, 3000, 9090 and 9100 |
| The VM runs out of memory | The Docker stack and the cluster together do not fit in 4 GB |
| The disk fills up | Docker images, volumes and the build cache took about 11 GB in your VM, and k3s downloads its own copy of the images |
| Pods cannot talk to each other | Docker changes the VM's firewall rules, and they can block the cluster network |
| You mix up the old and new setup | Two copies of videos, configs and exporters make troubleshooting hard |

**What you keep**

- The `~/streamedge` folder, including `ansible/`, `monitoring/`, `scripts/` and
  the hidden `.git` folder. Level 9 copies files from it, and git stores every
  level as a tag.
- Your published images on GitHub. They live online, not on the VM.

**What you remove**

- The Ansible deployment in `/opt/streamedge` (it only runs the system; the
  playbook files stay in `~/streamedge/ansible`)
- The old Level 1 to 8 containers, videos and logs
- Docker's images, volumes and cache
- The test clone VM (optional)

Do the sub-steps in order, and check the result after each one.

### 0.1 Make sure all your work is saved in git

**Why:** deleting is safe only if everything that matters is already on
GitHub.

```bash
cd ~/streamedge
git status
```

If it says "nothing to commit, working tree clean", go on. If it lists changes
you want to keep, save them first:

```bash
git add .
git commit -m "Save before cleanup"
git push
```

Then make sure Level 8 is tagged and pushed:

```bash
git tag level-8 2>/dev/null
git push origin level-8
git tag | tail -3
```

### 0.2 See what is running now

**Why:** you should know what you are about to remove.

```bash
docker ps -a
docker volume ls
systemctl is-active streamedge-exporter
```

### 0.3 Remove the Ansible deployment

**Why:** it holds the ports and memory that Kubernetes needs. `down -v` also
deletes its Prometheus and Grafana history, which you do not need.

```bash
cd /opt/streamedge
sudo docker compose down -v
sudo systemctl disable --now streamedge-exporter
sudo rm -f /etc/systemd/system/streamedge-exporter.service
sudo systemctl daemon-reload
```

Then remove the folder (this deletes the videos in it) and the service user:

```bash
cd ~
sudo rm -rf /opt/streamedge
sudo userdel streamedge
sudo groupdel streamedge 2>/dev/null
```

Check: `docker ps -a` lists nothing, and `ls /opt` does not show `streamedge`.

### 0.4 Remove the old Level 1 to 8 leftovers

**Why:** the old watcher and exporter scripts may still be running by hand, and
the old videos and logs use disk space.

```bash
pkill -f watcher.py
pkill -f exporter.py
cd ~/streamedge
docker compose down -v 2>/dev/null
rm -rf origin/www/video/* incoming/done incoming/failed
rm -f watcher.log exporter.log
```

(`pkill` printing nothing is fine.) This only removes videos and logs. All
configs and the `.gitkeep` files stay.

Also check your home folder for separate copies of old levels (for example
`~/level1` or `~/streamedge-old`):

```bash
ls ~
```

If you see such folders, and 0.1 showed a clean git state, you can delete them,
since every level is a git tag. Type the name exactly, and never
`~/streamedge`:

```bash
rm -rf ~/NAME-OF-OLD-FOLDER
```

### 0.5 Clean Docker completely

**Why:** this is the biggest space saver. Everything removed can be pulled
again from GitHub or rebuilt. k3s does not use these images.

First confirm nothing is running:

```bash
docker ps -a
```

If the list is empty:

```bash
docker system prune -a --volumes -f
docker system df
```

This cannot be undone, which is fine here. `docker system df` should now show
close to zero.

### 0.6 Remove the test clone VM (optional)

**Why:** a clone from Level 8 Experiment 6 takes disk space on your Windows PC
and is no longer needed.

1. In VirtualBox, power off the clone.
2. Right-click it, choose **Remove**, then **Delete all files**. Check that you
   select the clone and not your main VM.
3. If you added it to `~/streamedge/ansible/inventory.ini`, delete that line.

### 0.7 Stop the Docker service

**Why:** Kubernetes has its own container engine. Stopping Docker frees memory
and stops its firewall rules from interfering. Nothing is removed, and Docker
comes back when you re-run the Ansible playbook.

```bash
sudo systemctl disable --now docker docker.socket
systemctl is-active docker
```

The second command should print `inactive`.

### 0.8 Check the result

```bash
df -h /
free -h
ls ~
ls /opt
```

You should see much more free disk space, more than 2 GB in the "available"
column of `free -h`, only your `streamedge` repo in your home folder, and
nothing called `streamedge` in `/opt`.

If 0.1 showed files you are unsure about, paste me the `git status` output
before deleting anything.

## Step 1: Check Level 8 is tagged

You did the saving and tagging in Step 0.1. Just confirm:

```bash
cd ~/streamedge
git tag | grep level-8
```

It should print `level-8`.

## Step 2: Add the exporter image

Create the exporter files:

```bash
cd ~/streamedge
mkdir -p exporter
cat > exporter/exporter.py << 'EOF'
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
EOF
```

```bash
cat > exporter/Dockerfile << 'EOF'
# Built on the Varnish image so that varnishstat is available
FROM varnish:7.5
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*
COPY exporter.py /app/exporter.py
ENTRYPOINT ["python3", "-u", "/app/exporter.py"]
EOF
```

Teach the CI pipeline to build it. This edits `.github/workflows/ci.yml`
(adds the image to the build list and the new script to the syntax check):

```bash
python3 - << 'PY'
p = ".github/workflows/ci.yml"
s = open(p).read()
old = """          - name: watcher
            context: ./scripts
            file: ./scripts/Dockerfile.watcher
"""
new = old + """          - name: exporter
            context: ./exporter
            file: ./exporter/Dockerfile
"""
assert old in s, "watcher entry not found"
s = s.replace(old, new)
s = s.replace("python3 -m py_compile scripts/*.py", "python3 -m py_compile scripts/*.py exporter/*.py")
open(p, "w").write(s)
print("ci.yml updated")
PY
```

It should print `ci.yml updated`. Push it:

```bash
git add .
git commit -m "Level 9: add the exporter image to CI"
git push
```

Open the **Actions** tab and wait for all stages to turn green. Then open
your profile, **Packages**: there is a new package `streamedge-exporter`.
**Make it public**, as you did in Level 7, Step 6 (Package settings, Danger
zone, Change visibility, Public). Kubernetes pulls it without logging in.

## Step 3: Confirm the old setup is gone

Step 0 removed the Docker deployment, so this is only a quick check that
nothing holds the ports Kubernetes needs:

```bash
sudo ss -ltn | grep -E ':(8080|8404|3000|9090|9100) ' || echo "ports are free"
```

It should print `ports are free`. If it lists anything, go back to Step 0.3
and 0.4.

## Step 4: Install k3s

```bash
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable traefik --write-kubeconfig-mode 644" sh -
```

(`--disable traefik` leaves out a web router you do not need, which saves
memory. `--write-kubeconfig-mode 644` lets you use `kubectl` without sudo.)

Tell `kubectl` where the cluster settings are, for this and future logins:

```bash
echo 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml' >> ~/.bashrc
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl get nodes
```

After about 30 seconds the node should show `Ready`. Also look at what k3s
runs for itself:

```bash
kubectl get pods -A
```

## Step 5: Create the Kubernetes files

```bash
cd ~/streamedge
mkdir -p k8s/config
```

Namespace (a separate area for StreamEdge inside the cluster):

```bash
cat > k8s/namespace.yaml << 'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: streamedge
EOF
```

Origin (the video volume, the NGINX pod, and its Service):

```bash
cat > k8s/origin.yaml << 'EOF'
# Videos live on a persistent volume, shared by the origin and the watcher
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: video
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 5Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: origin
spec:
  replicas: 1
  selector:
    matchLabels:
      app: origin
  template:
    metadata:
      labels:
        app: origin
    spec:
      containers:
        - name: nginx
          image: ghcr.io/badyalrajesh/streamedge-origin
          ports:
            - containerPort: 80
          volumeMounts:
            - name: video
              mountPath: /usr/share/nginx/html/video
              readOnly: true
          readinessProbe:
            httpGet:
              path: /
              port: 80
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 10
            periodSeconds: 10
          resources:
            requests:
              cpu: 20m
              memory: 32Mi
            limits:
              memory: 128Mi
      volumes:
        - name: video
          persistentVolumeClaim:
            claimName: video
---
apiVersion: v1
kind: Service
metadata:
  name: origin
spec:
  selector:
    app: origin
  ports:
    - port: 80
      targetPort: 80
EOF
```

Edges (two Varnish pods, each with an exporter, and the headless Service):

```bash
cat > k8s/edge.yaml << 'EOF'
# Two Varnish edges. Each pod also runs an exporter container beside Varnish.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: edge
spec:
  replicas: 2
  selector:
    matchLabels:
      app: edge
  template:
    metadata:
      labels:
        app: edge
    spec:
      containers:
        - name: varnish
          image: ghcr.io/badyalrajesh/streamedge-edge
          env:
            - name: VARNISH_SIZE
              value: "64M"
          ports:
            - containerPort: 80
          volumeMounts:
            - name: varnish-work
              mountPath: /var/lib/varnish
          readinessProbe:
            httpGet:
              path: /healthz
              port: 80
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /healthz
              port: 80
            initialDelaySeconds: 10
            periodSeconds: 10
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              memory: 512Mi
        - name: exporter
          image: ghcr.io/badyalrajesh/streamedge-exporter
          ports:
            - containerPort: 9100
          volumeMounts:
            - name: varnish-work
              mountPath: /var/lib/varnish
          resources:
            requests:
              cpu: 10m
              memory: 32Mi
            limits:
              memory: 128Mi
      volumes:
        - name: varnish-work
          emptyDir: {}
---
# "Headless" service (clusterIP: None): DNS returns every edge pod's own
# address. HAProxy and Prometheus use this to find the edges.
apiVersion: v1
kind: Service
metadata:
  name: edge
spec:
  clusterIP: None
  selector:
    app: edge
  ports:
    - name: http
      port: 80
    - name: metrics
      port: 9100
EOF
```

Load balancer (HAProxy and its Service):

```bash
cat > k8s/lb.yaml << 'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: lb
spec:
  replicas: 1
  selector:
    matchLabels:
      app: lb
  template:
    metadata:
      labels:
        app: lb
    spec:
      containers:
        - name: haproxy
          image: ghcr.io/badyalrajesh/streamedge-lb
          ports:
            - name: web
              containerPort: 8080
            - name: stats
              containerPort: 8404
          volumeMounts:
            - name: haproxy-config
              mountPath: /usr/local/etc/haproxy
              readOnly: true
          readinessProbe:
            httpGet:
              path: /
              port: 8404
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /
              port: 8404
            initialDelaySeconds: 10
            periodSeconds: 10
          resources:
            requests:
              cpu: 20m
              memory: 32Mi
            limits:
              memory: 128Mi
      volumes:
        - name: haproxy-config
          configMap:
            name: haproxy-config
---
# type LoadBalancer: k3s opens these ports on the VM itself, so your
# VirtualBox rules for 8080 and 8404 keep working
apiVersion: v1
kind: Service
metadata:
  name: lb
spec:
  type: LoadBalancer
  selector:
    app: lb
  ports:
    - name: web
      port: 8080
      targetPort: 8080
    - name: stats
      port: 8404
      targetPort: 8404
EOF
```

Watcher (converts uploads):

```bash
cat > k8s/watcher.yaml << 'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: incoming
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: watcher
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: watcher
  template:
    metadata:
      labels:
        app: watcher
    spec:
      securityContext:
        runAsUser: 1500
        runAsGroup: 1500
        fsGroup: 1500
      containers:
        - name: watcher
          image: ghcr.io/badyalrajesh/streamedge-watcher
          volumeMounts:
            - name: incoming
              mountPath: /app/incoming
            - name: video
              mountPath: /app/origin/www/video
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              memory: 1Gi
      volumes:
        - name: incoming
          persistentVolumeClaim:
            claimName: incoming
        - name: video
          persistentVolumeClaim:
            claimName: video
EOF
```

Prometheus:

```bash
cat > k8s/prometheus.yaml << 'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: prometheus-data
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: prometheus
  template:
    metadata:
      labels:
        app: prometheus
    spec:
      securityContext:
        runAsUser: 65534
        runAsNonRoot: true
        fsGroup: 65534
      containers:
        - name: prometheus
          image: prom/prometheus:v2.53.0
          ports:
            - containerPort: 9090
          volumeMounts:
            - name: config
              mountPath: /etc/prometheus
              readOnly: true
            - name: data
              mountPath: /prometheus
          readinessProbe:
            httpGet:
              path: /-/ready
              port: 9090
            periodSeconds: 5
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              memory: 768Mi
      volumes:
        - name: config
          configMap:
            name: prometheus-config
        - name: data
          persistentVolumeClaim:
            claimName: prometheus-data
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus
spec:
  type: LoadBalancer
  selector:
    app: prometheus
  ports:
    - port: 9090
      targetPort: 9090
EOF
```

Grafana:

```bash
cat > k8s/grafana.yaml << 'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grafana
spec:
  replicas: 1
  selector:
    matchLabels:
      app: grafana
  template:
    metadata:
      labels:
        app: grafana
    spec:
      containers:
        - name: grafana
          image: grafana/grafana:11.1.0
          ports:
            - containerPort: 3000
          volumeMounts:
            - name: datasources
              mountPath: /etc/grafana/provisioning/datasources
              readOnly: true
            - name: dashboard-provider
              mountPath: /etc/grafana/provisioning/dashboards
              readOnly: true
            - name: dashboards
              mountPath: /etc/grafana/dashboards
              readOnly: true
            - name: data
              mountPath: /var/lib/grafana
          readinessProbe:
            httpGet:
              path: /api/health
              port: 3000
            periodSeconds: 5
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              memory: 512Mi
      volumes:
        - name: datasources
          configMap:
            name: grafana-datasources
        - name: dashboard-provider
          configMap:
            name: grafana-dashboard-provider
        - name: dashboards
          configMap:
            name: grafana-dashboards
        - name: data
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: grafana
spec:
  type: LoadBalancer
  selector:
    app: grafana
  ports:
    - port: 3000
      targetPort: 3000
EOF
```

The configuration files. HAProxy and Prometheus have Kubernetes-specific
versions; Grafana reuses the files you already have:

```bash
cat > k8s/config/haproxy.cfg << 'EOF'
global
    log stdout format raw local0

defaults
    mode http
    log global
    option httplog
    timeout connect 5s
    timeout client 30s
    timeout server 30s
    retries 2
    option redispatch

# Ask the cluster DNS which edge pods exist (changes as pods come and go)
resolvers kube
    parse-resolv-conf
    resolve_retries 3
    timeout resolve 1s
    timeout retry 1s
    hold valid 5s

frontend web
    bind *:8080
    default_backend edges

backend edges
    # Same URL always goes to the same edge. "consistent" means that adding
    # or removing an edge only moves a share of the URLs, not all of them.
    balance uri
    hash-type consistent
    option httpchk GET /healthz
    http-check expect status 200
    default-server inter 2s fall 2 rise 2
    # Up to 4 edge slots, filled automatically from DNS
    server-template edge 4 edge.streamedge.svc.cluster.local:80 check resolvers kube resolve-prefer ipv4 init-addr none
    http-response add-header X-Served-By %s

frontend stats
    bind *:8404
    http-request use-service prometheus-exporter if { path /metrics }
    stats enable
    stats uri /
    stats refresh 2s
EOF
```

```bash
cat > k8s/config/prometheus.yml << 'EOF'
global:
  scrape_interval: 5s
  scrape_timeout: 4s

scrape_configs:
  - job_name: haproxy
    static_configs:
      - targets: ["lb:8404"]

  # Find every edge pod through the headless service, and scrape the
  # exporter container in each one
  - job_name: varnish
    dns_sd_configs:
      - names: ["edge.streamedge.svc.cluster.local"]
        type: A
        port: 9100
    relabel_configs:
      - source_labels: [__address__]
        regex: "(.*):9100"
        target_label: edge
        replacement: "$1"
EOF
```

```bash
cp monitoring/grafana/dashboards/streamedge.json k8s/config/streamedge.json
cp monitoring/grafana/provisioning/datasources/datasource.yml k8s/config/grafana-datasource.yml
cp monitoring/grafana/provisioning/dashboards/dashboards.yml k8s/config/grafana-dashboards.yml
```

(Kustomize can only read files inside the `k8s/` folder, which is why the
Grafana files are copied.)

The file that ties everything together. **Check the image names**: they must
contain your GitHub username in lowercase. I used `badyalrajesh`:

```bash
cat > k8s/kustomization.yaml << 'EOF'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: streamedge

resources:
  - namespace.yaml
  - origin.yaml
  - edge.yaml
  - lb.yaml
  - watcher.yaml
  - prometheus.yaml
  - grafana.yaml

# Config files become ConfigMaps. Kustomize adds a hash to each name, so
# changing a file makes the pods that use it restart by themselves.
configMapGenerator:
  - name: haproxy-config
    files:
      - haproxy.cfg=config/haproxy.cfg
  - name: prometheus-config
    files:
      - prometheus.yml=config/prometheus.yml
  - name: grafana-datasources
    files:
      - datasource.yml=config/grafana-datasource.yml
  - name: grafana-dashboard-provider
    files:
      - dashboards.yml=config/grafana-dashboards.yml
  - name: grafana-dashboards
    files:
      - streamedge.json=config/streamedge.json

# Which version of each image to run. Change newTag to deploy another version.
images:
  - name: ghcr.io/badyalrajesh/streamedge-origin
    newTag: latest
  - name: ghcr.io/badyalrajesh/streamedge-edge
    newTag: latest
  - name: ghcr.io/badyalrajesh/streamedge-lb
    newTag: latest
  - name: ghcr.io/badyalrajesh/streamedge-watcher
    newTag: latest
  - name: ghcr.io/badyalrajesh/streamedge-exporter
    newTag: latest
EOF
```

Check everything is there:

```bash
find k8s -type f | sort
```

You should see 7 YAML files, `kustomization.yaml`, and 5 files in `config/`.

If your GitHub username is different, replace it in all files in one go:

```bash
sed -i 's/badyalrajesh/YOUR-USERNAME/g' k8s/*.yaml
```

## Step 6: Check the files before applying

```bash
kubectl kustomize k8s > /dev/null && echo "kustomize ok"
kubectl apply -k k8s --dry-run=client
```

The first should print `kustomize ok`. The second lists what would be
created. Read through the list: you should see the namespace, 6 deployments,
5 services, 3 volume claims and 5 config maps.

## Step 7: Start everything

```bash
kubectl apply -k k8s
kubectl config set-context --current --namespace=streamedge
kubectl get pods -w
```

(The second command makes `streamedge` the default namespace, so you do not
type `-n streamedge` every time.) The first start downloads the images, which
takes a few minutes. Watch the pods go through `ContainerCreating` to
`Running`, with `READY` showing `1/1` (edges show `2/2`: Varnish and the
exporter). Press Ctrl+C when they are all ready.

## Step 8: Check it works

```bash
kubectl get pods -o wide
kubectl get svc
curl -sI http://localhost:8080/ | head -1
curl -s http://localhost:8404/metrics | grep 'haproxy_server_status.*state="UP"'
```

You should see:
- All pods `Running`
- Services `lb`, `prometheus` and `grafana` with an EXTERNAL-IP and ports
- `HTTP/1.1 200 OK`
- Two `server="edge1"` and `server="edge2"` lines ending in `1`. (The slots
  `edge3` and `edge4` stay empty until you scale. HAProxy shows them as
  down. That is normal.)

Check Prometheus found both edge exporters:

```bash
curl -s http://localhost:9090/api/v1/targets | grep -o '"health":"[a-z]*"'
```

You should see `"health":"up"` three times (HAProxy and two edges).

The VirtualBox port rules from before still apply, because k3s opens the
ports on the VM itself. In your Windows browser open http://localhost:8080
(player), http://localhost:3000 (Grafana, login admin / admin) and
http://localhost:8404 (HAProxy stats).

## Step 9: Make a video inside the cluster

The player is empty at first. Create a test video inside the watcher pod,
which has ffmpeg:

```bash
kubectl exec deploy/watcher -- sh -c 'ffmpeg -loglevel error -f lavfi -i testsrc2=duration=15:size=640x360:rate=30 -f lavfi -i sine=frequency=550:duration=15 -c:v libx264 -g 30 -pix_fmt yuv420p -c:a aac /tmp/movie1.mp4 && mv /tmp/movie1.mp4 /app/incoming/movie1.mp4'
kubectl logs -f deploy/watcher
```

You should see "converting" and "published movie1". Press Ctrl+C, reload the
player (Ctrl+F5) and play it.

To upload your own file from the VM instead:

```bash
kubectl cp ~/myclip.mp4 $(kubectl get pod -l app=watcher -o name | cut -d/ -f2):/app/incoming/myclip.mp4
```

kubectl cp /home/adminuser/Downloads/sample-1.mp4 $(kubectl get pod -l app=watcher -o name | cut -d/ -f2):/app/incoming/sample-1.mp4

---

# Experiments

## Experiment 1: Look around

```bash
kubectl get pods -o wide
kubectl get pvc
kubectl describe deployment edge | head -30
kubectl get endpoints edge
```

`get endpoints edge` shows the addresses of the two edge pods. That is
exactly what HAProxy and Prometheus read through DNS.

## Experiment 2: Kubernetes heals itself

In a second terminal run a traffic loop:

```bash
while true; do curl -s -o /dev/null -w "%{http_code} " http://localhost:8080/video/movie1/seg_000.ts; sleep 0.3; done
```

In the first terminal delete one edge pod:

```bash
kubectl delete $(kubectl get pods -l app=edge -o name | head -1)
kubectl get pods -w
```

Kubernetes starts a replacement within seconds. The loop keeps printing
`200`, with at most a few errors. Watch the HAProxy stats page and the
Grafana dashboard: the edge goes down and a new one comes up. Press Ctrl+C
on both when you are done.

## Experiment 3: Scale the edges

```bash
kubectl scale deployment edge --replicas=3
kubectl get pods -l app=edge
```

After a few seconds a third edge is ready and HAProxy found it by itself,
with no config change:

```bash
curl -s http://localhost:8404/metrics | grep 'haproxy_server_status.*state="UP"'
```

Three servers (`edge1`, `edge2`, `edge3`) now end in `1`. To see consistent
hashing at work, run this before and after scaling and compare which edge
serves which file; most files stay on the same edge:

```bash
for n in 000 001 002; do echo -n "seg_$n -> "; curl -sI http://localhost:8080/video/movie1/seg_$n.ts | grep -i x-served-by; done
```

Scale back when you are done: `kubectl scale deployment edge --replicas=2`.

## Experiment 4: The origin goes down

First play the video once so the edges cache it. Then:

```bash
kubectl scale deployment origin --replicas=0
```

Play the video again. It still plays from the edge caches. Then bring the
origin back:

```bash
kubectl scale deployment origin --replicas=1
```

This is the CDN idea again, now in a cluster.

## Experiment 5: Change the config and roll it out

```bash
sed -i 's/balance uri/balance roundrobin/' k8s/config/haproxy.cfg
kubectl apply -k k8s
kubectl rollout status deployment/lb
kubectl get configmap
```

Kustomize made a new ConfigMap with a new hash in its name, so the `lb` pod
was replaced with a new one using the new config. Roll it back:

```bash
kubectl rollout history deployment/lb
kubectl rollout undo deployment/lb
```

Then put the file back to normal (`balance uri`) and apply again:

```bash
git checkout k8s/config/haproxy.cfg 2>/dev/null || sed -i 's/balance roundrobin/balance uri/' k8s/config/haproxy.cfg
kubectl apply -k k8s
```

## Experiment 6: CPU and memory

```bash
kubectl top nodes
kubectl top pods
free -h
```

This shows the CPU and memory of the node and of each pod. It answers the
earlier question of how much the system uses.

## Experiment 7: Deploy a specific version

Open the Packages page and copy a `sha-...` tag. Edit `newTag` for the edge
image in `k8s/kustomization.yaml` (use `nano`), then:

```bash
kubectl apply -k k8s
kubectl rollout status deployment/edge
kubectl get pods -l app=edge -o jsonpath='{range .items[*]}{.spec.containers[0].image}{"\n"}{end}'
```

The pods are replaced one at a time (a rolling update), so the service never
goes fully down. Set `newTag` back to `latest` and apply again. In Level 10,
changing this one line in git will be all it takes to deploy.

---

# Save Level 9 to GitHub

```bash
cd ~/streamedge
git add .
git commit -m "Level 9: Kubernetes manifests for k3s"
git tag level-9
git push
git push origin level-9
git ls-files k8s exporter
```

`git ls-files` should list the k8s files and the exporter files.

Update the level list at the top of README.md:

```
Levels
- level-1: NGINX origin + HLS video
- level-2: Varnish edge cache
- level-3: two edges + HAProxy failover
- level-4: auto ingest, purge, pre-warm
- level-5: Prometheus + Grafana monitoring
- level-6: Docker images with configs baked in
- level-7: CI with GitHub Actions, images published to GHCR
- level-8: Ansible playbook deploys the whole system
- level-9: Kubernetes (k3s) manifests with Kustomize
```

---

# Going back to the Docker deployment

```bash
sudo /usr/local/bin/k3s-killall.sh
cd ~/streamedge/ansible
ansible-playbook site.yml -K
```

`k3s-killall.sh` stops the cluster and frees the ports, and the playbook
starts the Docker version again (this also re-enables the exporter service).
To remove k3s completely, including the cluster's saved data:
`sudo /usr/local/bin/k3s-uninstall.sh`.

---

# If something goes wrong

- **`The connection to the server localhost:8080 was refused`:** `kubectl` does
  not know where the cluster is. Run
  `export KUBECONFIG=/etc/rancher/k3s/k3s.yaml`.
- **A pod shows `ImagePullBackOff`:** run `kubectl describe pod POD-NAME` and
  read the last lines. Usual causes: the package is private (the new
  `streamedge-exporter` one too), the image name has the wrong username, or
  CI has not finished publishing the exporter yet.
- **A pod stays `Pending`:** `kubectl describe pod POD-NAME`. It is usually
  not enough memory (`free -h`) or a volume that has not been created.
- **Edge pods show `CrashLoopBackOff`:** `kubectl logs deploy/edge -c varnish`
  shows why. Paste it to me if it is not obvious.
- **Edge pods run but `varnish_up` is 0 in the exporter:** check
  `kubectl logs deploy/edge -c exporter`, then
  `kubectl exec deploy/edge -c exporter -- varnishstat -1 | head -3`. If it
  cannot find the instance, show me
  `kubectl exec deploy/edge -c varnish -- ls /var/lib/varnish`.
- **HAProxy shows every edge as down:** `kubectl logs deploy/lb`, and check
  DNS from inside the cluster:
  `kubectl run dns --rm -it --image=busybox:1.36 --restart=Never -- nslookup edge.streamedge.svc.cluster.local`.
  It should print two addresses.
- **`503` on port 8080:** no edge is ready yet. `kubectl get pods`.
- **Windows browser cannot connect:** run `kubectl get svc`. The `lb` service
  needs an EXTERNAL-IP. Check no Docker container still holds the ports
  (`docker ps`), and that `kubectl get pods -n kube-system` shows `svclb` pods
  running.
- **Pods cannot reach each other or DNS times out:** Docker and k3s share the
  VM's firewall rules. Try `sudo systemctl restart k3s`. If it persists, run
  `sudo systemctl stop docker docker.socket` while you work on this level.
- **Not enough memory:** `free -h` and `kubectl top pods`. Check
  that Step 0 is done and scale the edges back to 2.
- **`kubectl apply -k` complains about a missing file:** run Step 5's copy
  commands again and check with `find k8s -type f | sort`.
- **Grafana has no data:** open http://localhost:9090/targets in Windows. All
  three targets should be UP. Wait a minute and press refresh in Grafana.

# Next: Level 10

GitOps with Argo CD. Instead of you running `kubectl apply`, Argo CD watches
your GitHub repository and keeps the cluster matching the `k8s/` folder.
Change one line in git, and the cluster updates by itself.


