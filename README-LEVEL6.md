# StreamEdge - Level 6: Build your own Docker images

This is the first stage of the full pipeline. Until now, Docker Compose ran
ready-made images (nginx, varnish, haproxy) and your config files were
attached from the outside. In Level 6 you build **your own images** with the
configs baked in. Every later stage (CI, Ansible, Kubernetes) depends on this.

```
The pipeline roadmap (one level at a time):

  Level 6   Docker images            <-- you are here
  Level 7   CI with GitHub Actions   test configs, build and publish images
  Level 8   Ansible                  fresh Ubuntu VM -> running StreamEdge
  Level 9   Kubernetes (k3s)         the same system as a cluster
  Level 10  GitOps with Argo CD      the cluster updates itself from git
  Level 11  Break it on purpose      failure experiments + postmortem
```

What changes:

```
Before (Level 5)                       After (Level 6)
nginx image + nginx.conf mounted   ->  streamedge/origin:dev  (config inside)
varnish image + default.vcl mounted -> streamedge/edge:dev    (config inside)
haproxy image + haproxy.cfg mounted -> streamedge/lb:dev      (config inside)
watcher.py run on the VM            ->  streamedge/watcher:dev (runs in Docker)
```

Files added or changed in this level:

```
streamedge/
  docker-compose.yml          CHANGED: builds the four images
  .env                        NEW: your user id (not committed)
  origin/
    Dockerfile                NEW
    .dockerignore             NEW: keeps video files out of the build
  edge/Dockerfile             NEW
  lb/Dockerfile               NEW
  scripts/
    Dockerfile.watcher        NEW
    watcher.py                CHANGED: videos.json moves into video/
  origin/www/index.html       CHANGED: reads /video/videos.json
```

Things you will learn:
- Image vs container, Dockerfile, build context, `.dockerignore`
- Layers and build caching
- Why configs inside an image are versioned together with the code
- A failed build as a safety net: a broken config never gets deployed

What stays outside the images, on purpose:
- **Videos** (large, change often) stay on the VM and are mounted in.
- **exporter.py, purge.py, prewarm.py** stay as scripts on the VM. They call
  `docker compose exec`, which a container can't do easily. In Level 9
  (Kubernetes) the exporter becomes a small container next to each edge.
- **Prometheus and Grafana** keep using the official images with mounted
  config files.

A trade-off to notice: after this level, editing a config is no longer
"edit the file and restart". It is "edit, build the image, redeploy". That
slower loop is what gives you versioned, repeatable releases.

---

# Steps (Ubuntu Server on VirtualBox)

Before you start, Level 5 must be working.

## Step 1: Save and tag Level 5 (skip if already done)

```bash
cd ~/streamedge
git add .
git commit -m "Level 5: Prometheus and Grafana monitoring dashboard"
git tag level-5
git push
git push origin level-5
```

If git says "nothing to commit", run the `git tag` and `git push` lines only.

## Step 2: Stop the watcher that runs on the VM

The watcher will run inside Docker from now on, so stop the old one:

```bash
pkill -f watcher.py
```

(It is fine if this prints nothing.)

## Step 3: Move videos.json into the video folder

The video folder is the only part of the origin that is mounted from the
VM. Moving `videos.json` into it lets the player page (index.html) live
inside the image. Two small edits and a cleanup:

```bash
cd ~/streamedge
sed -i 's#"www" / "videos.json"#"www" / "video" / "videos.json"#' scripts/watcher.py
sed -i 's#"/videos.json"#"/video/videos.json"#' origin/www/index.html
rm -f origin/www/videos.json
```

Check both edits took effect (each command should print one line):

```bash
grep -n "videos.json" scripts/watcher.py
grep -n "videos.json" origin/www/index.html
```

You should see `"www" / "video" / "videos.json"` and `"/video/videos.json"`.

## Step 4: Create the Dockerfiles

A Dockerfile is a recipe for an image: start from a base image, copy files
in, run commands.

**Origin image** (NGINX with your config and player page inside):

```bash
cat > origin/Dockerfile << 'EOF'
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY www/index.html /usr/share/nginx/html/index.html
# Fail the build if the config is broken
RUN nginx -t
EOF
```

Keep the video files out of the build. Without this, Docker would send
every video to the builder for nothing:

```bash
cat > origin/.dockerignore << 'EOF'
www/video
EOF
```

**Edge image** (Varnish with your config inside):

```bash
cat > edge/Dockerfile << 'EOF'
FROM varnish:7.5
COPY default.vcl /etc/varnish/default.vcl
EOF
```

**Load balancer image** (HAProxy with your config inside):

```bash
cat > lb/Dockerfile << 'EOF'
FROM haproxy:2.8
COPY haproxy.cfg /usr/local/etc/haproxy/haproxy.cfg
EOF
```

**Watcher image** (Python plus ffmpeg, runs your watcher.py):

```bash
cat > scripts/Dockerfile.watcher << 'EOF'
FROM python:3.12-slim

# Install ffmpeg first. This layer rarely changes, so Docker reuses it
# from its cache instead of downloading ffmpeg on every build.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# The script goes in last because it changes most often
COPY watcher.py /app/scripts/watcher.py

CMD ["python3", "-u", "/app/scripts/watcher.py"]
EOF
```

Check they all exist:

```bash
ls origin/Dockerfile edge/Dockerfile lb/Dockerfile scripts/Dockerfile.watcher
```

## Step 5: Tell Compose who you are

The watcher container writes files into your folders. Running it as your own
user keeps those files owned by you, not by root:

```bash
printf "HOST_UID=%s\nHOST_GID=%s\n" "$(id -u)" "$(id -g)" > .env
cat .env
echo ".env" >> .gitignore
```

You should see two lines such as `HOST_UID=1000` and `HOST_GID=1000`.

## Step 6: Replace docker-compose.yml

```bash
cat > docker-compose.yml << 'EOF'
services:
  origin:
    build: ./origin
    image: streamedge/origin:dev
    ports:
      - "8081:80"
    volumes:
      - ./origin/www/video:/usr/share/nginx/html/video:ro

  edge1:
    build: ./edge
    image: streamedge/edge:dev
    ports:
      - "127.0.0.1:8082:80"
    depends_on:
      - origin

  edge2:
    build: ./edge
    image: streamedge/edge:dev
    ports:
      - "127.0.0.1:8083:80"
    depends_on:
      - origin

  lb:
    build: ./lb
    image: streamedge/lb:dev
    ports:
      - "8080:8080"
      - "8404:8404"
    depends_on:
      - edge1
      - edge2

  watcher:
    build:
      context: ./scripts
      dockerfile: Dockerfile.watcher
    image: streamedge/watcher:dev
    user: "${HOST_UID}:${HOST_GID}"
    volumes:
      - ./incoming:/app/incoming
      - ./origin/www/video:/app/origin/www/video
    restart: unless-stopped

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

What is new: `build:` tells Compose to build the image from a Dockerfile, and
`image:` gives it a name. `edge1` and `edge2` use the same image. The config
mounts for origin, edges and lb are gone because the configs are now inside.

## Step 7: Build the images

```bash
docker compose build
```

The first build takes a few minutes (the watcher image downloads ffmpeg).
Watch the output: each Dockerfile line is a "step", and Docker saves the
result of each step as a layer.

List what you built:

```bash
docker images | grep streamedge
```

You should see four images: origin, edge, lb and watcher, all tagged `dev`.
Note the SIZE column. The watcher is the biggest because of ffmpeg.

## Step 8: Start everything

```bash
docker compose up -d
docker compose ps
```

You should see seven containers running: origin, edge1, edge2, lb, watcher,
prometheus and grafana. The edges restart with empty caches because their
image changed.

Check the watcher started and found your videos:

```bash
docker compose logs watcher
cat origin/www/video/videos.json
```

You should see "watching ..." in the log, and `["movie2","sample"]` (or
whichever videos you have) in the file.

Then open http://localhost:8080 on Windows and press Ctrl+F5. The video
dropdown should work as before.

---

# Experiments

## Experiment 1: Layers and the build cache

Run the build a second time, with nothing changed:

```bash
docker compose build
```

Every step is reported as cached and it finishes in seconds. Now change one
config and build again:

```bash
echo "# test change" >> edge/default.vcl
docker compose build edge1
```

Only the `COPY` step re-runs. Docker reuses everything before it. That is
also why the watcher Dockerfile installs ffmpeg before copying the script.
Undo the change:

```bash
sed -i '$d' edge/default.vcl
tail -3 edge/default.vcl
```

(The last line of the file should be `}` again.)

## Experiment 2: Look inside an image

The config really is inside the image now:

```bash
docker run --rm --entrypoint cat streamedge/edge:dev /etc/varnish/default.vcl
```

That prints your VCL straight from the image, with no file on the VM
involved. See how the image was assembled:

```bash
docker history streamedge/origin:dev
```

Each row is a layer, with its size. Your `COPY` lines are tiny.

## Experiment 3: A broken config can never be deployed

Add a deliberate mistake to the origin config and try to build:

```bash
echo "oops" >> origin/nginx.conf
docker compose build origin
```

The build **fails** at the `RUN nginx -t` step with an error, and no image
is produced. Compare with Level 5, where a broken file would only show up
when the container crashed. Fix it:

```bash
sed -i '$d' origin/nginx.conf
docker compose build origin
```

This idea, "catch bad config before it ships", is the heart of CI in Level 7.

## Experiment 4: The new edit, build, deploy loop

Make a real change and release it:

1. Edit `edge/default.vcl` (for example, change `grace` from `1h` to `2h`)
2. `docker compose build edge1 edge2`
3. `docker compose up -d`

Only the edges are recreated; the rest keep running. Check the new image is
in use. Then revert the change and redeploy the same way.

Notice you could now tag this version and go back to it at any time. In
Level 7 each version gets its own tag automatically.

## Experiment 5: The watcher now lives in Docker

Add a video the same way as in Level 4, Experiment 1:

```bash
ffmpeg -f lavfi -i testsrc2=duration=15:size=640x360:rate=30 \
       -f lavfi -i sine=frequency=550:duration=15 \
       -c:v libx264 -pix_fmt yuv420p -c:a aac /tmp/movie3.mp4
mv /tmp/movie3.mp4 incoming/
docker compose logs -f watcher
```

You should see "converting", "published movie3" and a new videos.json line.
Press Ctrl+C to leave the log view (the watcher keeps running). Reload the
player: `movie3` is in the dropdown.

The conversion ran inside the container with ffmpeg from the image, so
ffmpeg no longer needs to be installed on the VM at all (you only used it
here to make a test clip).

Also try: `docker compose stop watcher`, drop another file in, and notice
nothing happens until `docker compose start watcher`. Services are
independent now.

---

# Save Level 6 to GitHub

```bash
cd ~/streamedge
git add .
git commit -m "Level 6: Docker images for origin, edge, lb and watcher"
git tag level-6
git push
git push origin level-6
git ls-files
```

`git ls-files` should include the four Dockerfiles and `.dockerignore`, and
should not include `.env` or any video files.

Update the level list at the top of README.md:

```
Levels
- level-1: NGINX origin + HLS video
- level-2: Varnish edge cache
- level-3: two edges + HAProxy failover
- level-4: auto ingest, purge, pre-warm
- level-5: Prometheus + Grafana monitoring
- level-6: Docker images with configs baked in
```

---

# After you restart the Ubuntu VM

```bash
cd ~/streamedge
docker compose up -d
nohup python3 scripts/exporter.py > exporter.log 2>&1 &
```

The watcher now starts with Compose. Only the exporter still runs as a
script on the VM (until Level 9).

---

# If something goes wrong

- `variable HOST_UID is not set` warning: the `.env` file is missing or you
  are in the wrong folder. Repeat Step 5 and run commands from `~/streamedge`.
- The build fails at `RUN nginx -t`: your `origin/nginx.conf` has an error.
  The message names the line.
- The watcher container keeps restarting: run `docker compose logs watcher`.
  A `PermissionError` means the folders are owned by root. Fix with:
  `sudo chown -R $USER: incoming origin/www/video`
- The dropdown is empty: check `cat origin/www/video/videos.json`. If it is
  missing, the watcher is not running (`docker compose logs watcher`). Also
  press Ctrl+F5 in the browser.
- Page works but videos do not play: run `ls origin/www/video/sample`. The
  segment files must be there. Check that `docker compose ps` shows origin
  running.
- `build` is very slow or sends a huge "context": the `.dockerignore` in
  `origin/` is missing or misnamed. It must be named `.dockerignore`.
- Port already in use: run `docker compose down`, then `up -d` again. Also
  check that the old watcher script is not running: `pgrep -f watcher.py`
  should print nothing.
- Image pull fails: base image tags can change. Try `python:3.12-slim`,
  `nginx:1.27-alpine`, `varnish:stable`, or `haproxy:2.8` with a newer tag.
- Prometheus targets go down after the restart: the exporter is a script on
  the VM; start it again with the command from the "After you restart" part.

# Next: Level 7

CI with GitHub Actions. On every `git push`, GitHub checks your configs,
builds these four images and publishes them to a registry, each tagged with
the version of the code that produced it. You will see your first green
check mark on GitHub.

