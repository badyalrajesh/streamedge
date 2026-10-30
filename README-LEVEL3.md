# StreamEdge - Level 3: Two edges + HAProxy load balancer

Level 2 had one edge cache. If it died, viewers lost the video. Level 3 adds
a second edge and a load balancer (HAProxy) in front. HAProxy sends viewers
to a healthy edge and stops sending to one that dies. This is failover.

```
Level 2:  Browser ---> Varnish edge ---> NGINX origin

Level 3:                 +--> edge1 --+
          Browser ---> HAProxy        +--> NGINX origin
          (port 8080)    +--> edge2 --+
          stats page: port 8404
```

Files added or changed in this level:

```
streamedge/
  docker-compose.yml     CHANGED: edge1, edge2, lb (HAProxy)
  edge/default.vcl       CHANGED: adds a /healthz health-check path
  lb/
    haproxy.cfg          NEW: load balancer + health checks + stats page
```

What you learn: load balancing, health checks, failover, cold caches after
a restart, and why the way you spread requests changes your hit ratio.

---

# Steps (Ubuntu Server on VirtualBox)

Before you start, Level 2 must be working and saved.

## Step 1: Save and tag Level 2 (skip if already done)

```bash
cd ~/streamedge
git add .
git commit -m "Level 2: Varnish edge cache with HIT/MISS headers"
git tag level-2
git push
git push origin level-2
```

If git says "nothing to commit", run the `git tag` and `git push` lines only.

## Step 2: Stop Level 2

```bash
docker compose down
```

## Step 3: Update the edge config (adds a health-check path)

HAProxy needs a quick way to ask "are you alive?". The edge answers
/healthz itself, without touching the origin, so an edge counts as healthy
even when the origin is down.

```bash
cat > edge/default.vcl << 'EOF'
vcl 4.1;

backend origin {
    .host = "origin";
    .port = "80";
}

sub vcl_recv {
    # Health check for the load balancer
    if (req.url == "/healthz") {
        return (synth(200, "OK"));
    }
}

sub vcl_backend_response {
    # If origin goes down, keep serving cached copies for up to 1 hour
    set beresp.grace = 1h;
}

sub vcl_deliver {
    # Add headers so we can SEE what the cache did
    if (obj.hits > 0) {
        set resp.http.X-Cache = "HIT";
    } else {
        set resp.http.X-Cache = "MISS";
    }
    set resp.http.X-Cache-Hits = obj.hits;
}
EOF
```

## Step 4: Create the load balancer config

```bash
mkdir lb
cat > lb/haproxy.cfg << 'EOF'
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

frontend web
    bind *:8080
    default_backend edges

backend edges
    balance roundrobin
    option httpchk GET /healthz
    http-check expect status 200
    default-server inter 2s fall 2 rise 2
    server edge1 edge1:80 check
    server edge2 edge2:80 check
    http-response add-header X-Served-By %s

frontend stats
    bind *:8404
    stats enable
    stats uri /
    stats refresh 2s
EOF
```

What the key lines mean:
- `balance roundrobin`: take turns between the edges.
- `check` + `inter 2s fall 2 rise 2`: test each edge every 2 seconds. Two
  failures = DOWN. Two successes = UP again.
- `X-Served-By`: a header that tells you which edge answered.

## Step 5: Replace docker-compose.yml

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
    volumes:
      - ./edge/default.vcl:/etc/varnish/default.vcl:ro
    depends_on:
      - origin

  edge2:
    image: varnish:7.5
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
EOF
```

The two edges no longer publish ports. Only HAProxy is reachable from
outside, which is how a real CDN front door works.

## Step 6: Start Level 3

```bash
docker compose up -d --remove-orphans
docker compose ps
```

You should see 4 containers running: origin, edge1, edge2, lb.
`--remove-orphans` clears the old single `edge` container from Level 2.

Test it:

```bash
curl -sI http://localhost:8080/video/index.m3u8 | grep -i -E "HTTP|x-served-by|x-cache:"
```

You should see `200 OK` and an `X-Served-By: edge1` or `edge2` line.

## Step 7: Open the stats page from Windows

Add one more VirtualBox port-forward rule (Settings, Network, Adapter 1,
Advanced, Port Forwarding): TCP, Host Port 8404, Guest Port 8404.

Then open http://localhost:8404 in your Windows browser. You will see edge1
and edge2 in green. Keep this tab open for the experiments.

The video still plays at http://localhost:8080 as before.

---

# Experiments

## Experiment 1: Watch the load balancer take turns

```bash
for i in 1 2 3 4 5 6; do curl -sI http://localhost:8080/video/seg_000.ts | grep -i -E "x-served-by|x-cache:"; done
```

You'll see edge1 and edge2 alternate. Each edge gets its own first MISS,
then HITs. The same file is now cached twice, once per edge.

## Experiment 2: Kill an edge (failover)

Keep the stats page visible, then:

```bash
docker compose stop edge1
```

Within about 4 seconds edge1 turns red (DOWN) on the stats page. Now run:

```bash
for i in 1 2 3 4 5 6; do curl -sI http://localhost:8080/video/seg_000.ts | grep -i -E "HTTP|x-served-by"; done
```

Every answer comes from edge2 and the video keeps playing. A request made
in the first couple of seconds may take longer while HAProxy notices;
`retries` and `redispatch` in the config cover that.

## Experiment 3: Bring it back and see a cold cache

```bash
docker compose start edge1
```

After about 4 seconds edge1 turns green. Request the same file a few
times: edge1 shows `MISS` again, because its cache lived in RAM and was
lost. Edge2 still shows `HIT`. This is a "cold cache" and it is why
real CDNs worry about restarting servers during busy hours.

## Experiment 4: Compare the edges' hit ratio

```bash
docker compose exec edge1 varnishstat -1 -f MAIN.cache_hit -f MAIN.cache_miss
docker compose exec edge2 varnishstat -1 -f MAIN.cache_hit -f MAIN.cache_miss
```

Write both numbers down. You will compare them in the next experiment.

## Experiment 5: Improve the hit ratio

Round robin caches every file on both edges, which wastes memory and
causes extra misses. A better way is to always send the same URL to the
same edge. Change one line in lb/haproxy.cfg:

```bash
sed -i 's/balance roundrobin/balance uri/' lb/haproxy.cfg
docker compose restart lb
docker compose restart edge1 edge2
```

(The edge restart empties both caches so the test starts fair.) Then:

```bash
for n in 000 001 002 003; do for i in 1 2 3; do curl -sI http://localhost:8080/video/seg_$n.ts | grep -i x-served-by; done; done
```

Now each segment is always served by the same edge. Each file is cached
once instead of twice. Run Experiment 4 again to compare.

Trade-off to remember: with `balance uri`, if the edge holding a popular
file dies, its files must be re-cached on the other edge. Real CDNs use
"consistent hashing" to reduce that shock.

---

# Save Level 3 to GitHub

```bash
cd ~/streamedge
git add .
git commit -m "Level 3: two Varnish edges behind HAProxy with failover"
git tag level-3
git push
git push origin level-3
```

Update the level list at the top of README.md:

```
Levels
- level-1: NGINX origin + HLS video
- level-2: Varnish edge cache
- level-3: two edges + HAProxy failover
```

---

# If something goes wrong

- `lb` exits right away: run `docker compose logs lb`. A config error shows
  the line number. Recheck lb/haproxy.cfg was pasted exactly.
- Both edges show red on the stats page: run `docker compose logs edge1`.
  The edge may have failed to start due to a VCL typo.
- An edge stays red after `docker compose start`: run
  `docker compose restart lb` (HAProxy cached the old address).
- Port 8080 busy: run `docker compose down`, then try again.
- Can't open the stats page from Windows: recheck the port 8404 rule.
- No X-Served-By header: make sure you used port 8080 (HAProxy).
- Varnish image fails to pull: change `varnish:7.5` to `varnish:stable`.

# Next: Level 4

A Python script that watches the `incoming` folder, converts new videos
automatically, and a purge script that removes a video from both edges.
