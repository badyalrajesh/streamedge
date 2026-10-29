# StreamEdge - Level 2: Varnish edge cache

Level 1 served an HLS video from one NGINX origin. Level 2 puts a Varnish
edge cache in front of it, so you can watch a CDN's core idea working:
the first request is a MISS (goes to the origin), later requests are a HIT
(answered from the edge's memory).

```
Level 1:  Browser ---> NGINX origin
Level 2:  Browser ---> Varnish edge ---> NGINX origin
                       (port 8080)       (port 8081, for testing only)
```

Files added or changed in this level:

```
streamedge/
  docker-compose.yml     CHANGED: adds the edge, origin moves to 8081
  edge/
    default.vcl          NEW: Varnish config (HIT/MISS headers, 1h grace)
  origin/www/index.html  CHANGED: adds a "Check cache" button
```

What you learn: cache HIT vs MISS, how Cache-Control sets cache lifetime,
cache hit ratio, and how an edge keeps serving when the origin is down.

---

# Steps (Ubuntu Server on VirtualBox)

Before you start, Level 1 must be working and saved:

- Video plays at http://localhost:8080 from Windows
- Level 1 is committed and tagged (see Step 1 below)

## Step 1: Save and tag Level 1 (skip if already done)

```bash
cd ~/streamedge
git add .
git commit -m "Level 1: NGINX origin with HLS playback"
git tag level-1
git push
git push origin level-1
```

If git says "nothing to commit", that's fine. Run the `git tag` and
`git push` lines only.

## Step 2: Stop Level 1

Level 2 uses the same port, so stop Level 1 first.

```bash
docker compose down
```

## Step 3: Create the edge config

```bash
cd ~/streamedge
mkdir edge
cat > edge/default.vcl << 'EOF'
vcl 4.1;

backend origin {
    .host = "origin";
    .port = "80";
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

Varnish follows the Cache-Control headers from nginx.conf: 2 seconds for
the manifest (.m3u8) and 1 hour for segments (.ts).

## Step 4: Replace docker-compose.yml

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

  edge:
    image: varnish:7.5
    ports:
      - "8080:80"
    volumes:
      - ./edge/default.vcl:/etc/varnish/default.vcl:ro
    depends_on:
      - origin
EOF
```

The edge takes port 8080, so your VirtualBox port-forward rule from Level 1
still works. The origin moves to 8081 (use it only for testing inside Ubuntu).

## Step 5: Replace the player page (adds a "Check cache" button)

```bash
cat > origin/www/index.html << 'EOF'
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>StreamEdge</title></head>
<body>
  <h1>StreamEdge</h1>
  <video id="v" controls width="640"></video>
  <p>
    <button id="chk">Check cache for segment 0</button>
    <span id="out"></span>
  </p>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js"></script>
  <script>
    const src = "/video/index.m3u8";
    const v = document.getElementById("v");
    if (Hls.isSupported()) {
      const hls = new Hls();
      hls.loadSource(src);
      hls.attachMedia(v);
    } else {
      v.src = src;
    }
    document.getElementById("chk").onclick = async () => {
      const r = await fetch("/video/seg_000.ts", { cache: "no-store" });
      document.getElementById("out").textContent =
        "X-Cache: " + r.headers.get("X-Cache") +
        " | Hits: " + r.headers.get("X-Cache-Hits") +
        " | Age: " + r.headers.get("Age") + "s";
    };
  </script>
</body>
</html>
EOF
```

## Step 6: Start Level 2

```bash
docker compose up -d
docker compose ps
```

Both `origin` and `edge` should show as running. If the Varnish image
fails to pull, change `varnish:7.5` to `varnish:stable` in
docker-compose.yml and run the command again.

Then open http://localhost:8080 on Windows and press play.

---

# Experiments

## Experiment 1: Watch MISS turn into HIT

Run this twice:

```bash
curl -sI http://localhost:8080/video/seg_000.ts | grep -i -E "x-cache|age"
```

First run: `X-Cache: MISS`. Second run: `X-Cache: HIT` and a growing `Age`.

## Experiment 2: Prove the origin is left alone

```bash
docker compose logs -f origin
```

Play the video in the Windows browser, then refresh and play again. No new
origin log lines appear for the segments the second time. Press Ctrl+C to
leave the log view.

## Experiment 3: Get your cache hit ratio

```bash
docker compose exec edge varnishstat -1 -f MAIN.cache_hit -f MAIN.cache_miss
```

Hit ratio = hits / (hits + misses). Write your number down.

## Experiment 4: Check the 1 hour cache time

The origin sets the lifetime:

```bash
curl -sI http://localhost:8081/video/seg_000.ts | grep -i cache-control
```

Expect `max-age=3600` (1 hour). The edge shows how old its copy is:

```bash
curl -sI http://localhost:8080/video/seg_000.ts | grep -i -E "x-cache|age"
```

When `Age` reaches 3600, the edge fetches a fresh copy from the origin.
The manifest uses `max-age=2`, so its Age never gets past 2 seconds.

## Experiment 5: Kill the origin

```bash
docker compose stop origin
```

Reload http://localhost:8080 and play the video. The edge has everything
cached plus a 1 hour grace period, so it should keep playing. Bring the
origin back:

```bash
docker compose start origin
```

---

# Save Level 2 to GitHub

```bash
cd ~/streamedge
git add .
git commit -m "Level 2: Varnish edge cache with HIT/MISS headers"
git tag level-2
git push
git push origin level-2
```

## Go back to Level 1 later

```bash
docker compose down
git checkout level-1
docker compose up -d
```

Return to the latest version:

```bash
docker compose down
git checkout main
docker compose up -d
```

---

# If something goes wrong

- Port 8080 or 8081 busy: run `docker compose down` first, then try again.
- No X-Cache header: check you are using port 8080 (edge), not 8081 (origin).
- Edge exits right away: run `docker compose logs edge` and look for a
  VCL error; recheck edge/default.vcl was pasted exactly.
- `varnishstat` shows 0 hits: request the same file twice via port 8080.
- Video does not play after stopping origin: run Experiment 1 first so the
  segments are cached, then retry.

# Next: Level 3

A second edge plus HAProxy in front, then kill one edge and watch failover.
