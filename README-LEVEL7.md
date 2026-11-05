[200~# StreamEdge - Level 7: CI with GitHub Actions

Level 6 turned your system into four Docker images, but you still build and
check them by hand. Level 7 makes GitHub do it automatically on every push.
This is **CI (Continuous Integration)**: every change is tested the moment it
is pushed, and only good changes produce images.

```
The pipeline roadmap:

  Level 6   Docker images
  Level 7   CI with GitHub Actions   <-- you are here
  Level 8   Ansible                  fresh Ubuntu VM -> running StreamEdge
  Level 9   Kubernetes (k3s)         the same system as a cluster
  Level 10  GitOps with Argo CD      the cluster updates itself from git
  Level 11  Break it on purpose      failure experiments + postmortem
```

What happens on every push:

```
git push
   |
   v
[1] Check configs      nginx, haproxy, varnish, prometheus, python, json
   |  (stops here if any check fails)
   v
[2] Run and test       start the real system on a GitHub server, then test:
   |                   video is served, cache hits work, both edges healthy,
   |                   traffic keeps working when an edge is stopped
   v
[3] Build 4 images     origin, edge, lb, watcher
       |
       +--> pull request: build only (proves the images build)
       +--> push to main: also publish to GitHub Container Registry
```

Published images (GitHub Container Registry, like AWS ECR):

```
ghcr.io/YOUR-USERNAME/streamedge-origin
ghcr.io/YOUR-USERNAME/streamedge-edge
ghcr.io/YOUR-USERNAME/streamedge-lb
ghcr.io/YOUR-USERNAME/streamedge-watcher
```

Each image gets tags such as `sha-1a2b3c4` (the exact commit that built it),
`latest` (newest main), and `v1.0.0` (when you tag a release). A tag tells you
exactly which code is inside an image, which Levels 8 to 10 depend on.

Files added in this level:

```
streamedge/
  .github/
    workflows/
      ci.yml        NEW: the whole pipeline
```

Things you will learn:
- Workflows, jobs, steps and runners in GitHub Actions
- Testing configs before they ship (including the load balancer and the
  cache config) and running a real integration test in CI
- Pull requests as a gate: a red check blocks bad changes
- Container registries, image tags and why `latest` alone is not enough
- Build caching in CI

Cost: GitHub Actions is free for public repositories. Private repositories
have a monthly free allowance (this pipeline uses a few minutes per run).

---

# Steps (Ubuntu Server on VirtualBox)

Before you start, Level 6 must be working and pushed to GitHub.

## Important first: your GitHub token must allow workflow files

GitHub refuses to accept a push that contains a file under
`.github/workflows/` unless the token has permission for workflows. The token
you made in Level 1 probably has only "Contents: Read and write".

Fix it before pushing: on github.com open Settings, Developer settings,
Personal access tokens, Fine-grained tokens, click your token, and under
Repository permissions set **Workflows** to **Read and write**. Save.
(If you made a classic token, tick the `workflow` scope instead.)

## Step 1: Save and tag Level 6 (skip if already done)

```bash
cd ~/streamedge
git add .
git commit -m "Level 6: Docker images for origin, edge, lb and watcher"
git tag level-6
git push
git push origin level-6
```

If git says "nothing to commit", run the `git tag` and `git push` lines only.

## Step 2: Check your branch is called main

```bash
git branch --show-current
```

It should print `main`. If it prints `master`, rename it and push:
`git branch -M main` then `git push -u origin main`.

## Step 3: Create the workflow file

GitHub looks for workflows in exactly this folder, so the path matters:

```bash
cd ~/streamedge
mkdir -p .github/workflows
cat > .github/workflows/ci.yml << 'EOF'
name: CI

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  # ---------------------------------------------------------------
  # 1. Check every config file before anything is built
  # ---------------------------------------------------------------
  validate:
    name: Check configs
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Python scripts compile
        run: python3 -m py_compile scripts/*.py

      - name: Grafana dashboard is valid JSON
        run: python3 -c "import json; json.load(open('monitoring/grafana/dashboards/streamedge.json'))"

      - name: NGINX config
        run: |
          docker run --rm \
            -v "$PWD/origin/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
            nginx:1.27-alpine nginx -t

      - name: HAProxy config
        run: |
          docker run --rm \
            --add-host edge1:127.0.0.1 --add-host edge2:127.0.0.1 \
            -v "$PWD/lb/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro" \
            haproxy:2.8 haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg

      - name: Varnish config
        run: |
          docker run --rm \
            --add-host origin:127.0.0.1 \
            -v "$PWD/edge/default.vcl:/etc/varnish/default.vcl:ro" \
            --entrypoint varnishd \
            varnish:7.5 -C -f /etc/varnish/default.vcl -n /tmp/vtest > /dev/null

      - name: Prometheus config
        run: |
          docker run --rm \
            -v "$PWD/monitoring:/m:ro" \
            --entrypoint promtool \
            prom/prometheus:v2.53.0 check config /m/prometheus.yml

      - name: Compose file
        run: |
          printf "HOST_UID=1000\nHOST_GID=1000\n" > .env
          docker compose config -q

  # ---------------------------------------------------------------
  # 2. Start the real system and test it
  # ---------------------------------------------------------------
  smoke-test:
    name: Run the system and test it
    runs-on: ubuntu-latest
    needs: validate
    steps:
      - uses: actions/checkout@v4

      - name: Make a tiny test video
        run: |
          sudo apt-get update -qq
          sudo apt-get install -y -qq ffmpeg
          mkdir -p origin/www/video/sample
          ffmpeg -loglevel error \
            -f lavfi -i testsrc=duration=12:size=320x180:rate=15 \
            -f lavfi -i sine=frequency=440:duration=12 \
            -c:v libx264 -g 15 -keyint_min 15 -sc_threshold 0 -pix_fmt yuv420p -c:a aac \
            -f hls -hls_time 4 -hls_playlist_type vod \
            -hls_segment_filename origin/www/video/sample/seg_%03d.ts \
            origin/www/video/sample/index.m3u8

      - name: Build and start origin, edges and load balancer
        run: |
          printf "HOST_UID=1000\nHOST_GID=1000\n" > .env
          docker compose up -d --build origin edge1 edge2 lb

      - name: Wait until the system answers
        run: |
          for i in $(seq 1 30); do
            if curl -sf -o /dev/null http://localhost:8080/video/sample/index.m3u8; then
              echo "up after $i tries"
              exit 0
            fi
            sleep 2
          done
          echo "system did not come up"
          exit 1

      - name: Test - segment is served and gets cached
        run: |
          code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/video/sample/seg_000.ts)
          echo "first request -> $code"
          test "$code" = "200"
          hits=0
          for i in 1 2 3 4; do
            if curl -sI http://localhost:8080/video/sample/seg_000.ts | grep -qi "^x-cache: HIT"; then
              hits=$((hits+1))
            fi
          done
          echo "cache hits out of 4 requests: $hits"
          test "$hits" -ge 1

      - name: Test - both edges report healthy
        run: |
          for i in $(seq 1 15); do
            up=$(curl -s http://localhost:8404/metrics | grep -c '^haproxy_server_status{proxy="edges",server="edge[12]",state="UP"} 1' || true)
            if [ "$up" = "2" ]; then
              echo "both edges are UP"
              exit 0
            fi
            sleep 2
          done
          echo "edges are not healthy"
          exit 1

      - name: Test - failover when one edge stops
        run: |
          docker compose stop edge1
          sleep 8
          for i in 1 2 3 4 5 6; do
            code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/video/sample/seg_00$((i % 3)).ts)
            echo "request $i -> $code"
            test "$code" = "200"
          done

      - name: Show logs if something failed
        if: failure()
        run: docker compose logs --no-color | tail -100

  # ---------------------------------------------------------------
  # 3. Build the images; publish them unless this is a pull request
  # ---------------------------------------------------------------
  publish:
    name: Image ${{ matrix.name }}
    runs-on: ubuntu-latest
    needs: [validate, smoke-test]
    permissions:
      contents: read
      packages: write
    strategy:
      matrix:
        include:
          - name: origin
            context: ./origin
            file: ./origin/Dockerfile
          - name: edge
            context: ./edge
            file: ./edge/Dockerfile
          - name: lb
            context: ./lb
            file: ./lb/Dockerfile
          - name: watcher
            context: ./scripts
            file: ./scripts/Dockerfile.watcher
    steps:
      - uses: actions/checkout@v4

      - name: Lowercase the owner name (registry names must be lowercase)
        run: echo "OWNER=${GITHUB_REPOSITORY_OWNER,,}" >> "$GITHUB_ENV"

      - uses: docker/setup-buildx-action@v3

      - name: Log in to GitHub Container Registry
        if: github.event_name != 'pull_request'
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Work out the image tags
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ env.OWNER }}/streamedge-${{ matrix.name }}
          tags: |
            type=sha,format=short
            type=raw,value=latest,enable={{is_default_branch}}
            type=ref,event=tag

      - name: Build (and push, except on pull requests)
        uses: docker/build-push-action@v6
        with:
          context: ${{ matrix.context }}
          file: ${{ matrix.file }}
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha,scope=${{ matrix.name }}
          cache-to: type=gha,mode=max,scope=${{ matrix.name }}
EOF
```

Check it:

```bash
ls .github/workflows
head -12 .github/workflows/ci.yml
```

Spaces matter in YAML. Paste the block exactly as it is.

## Step 4: Push it and watch GitHub run it

```bash
git add .
git commit -m "Level 7: CI pipeline with GitHub Actions"
git push
```

Now open your repository on github.com in your Windows browser and click the
**Actions** tab. You will see a run called "Level 7: CI pipeline with GitHub
Actions" with a yellow dot (running). Click it.

You will see three stages: "Check configs", "Run the system and test it",
and "Image origin / edge / lb / watcher" (four builds running side by
side). A first run takes about 5 to 8 minutes. When everything has a green
check mark, the pipeline passed.

Click into "Run the system and test it" and expand the steps. You will see
the output of each test, for example the failover requests all returning 200.

## Step 5: See your published images

On github.com, click your profile picture, then Your profile, then the
**Packages** tab. You should see four packages: `streamedge-origin`,
`streamedge-edge`, `streamedge-lb` and `streamedge-watcher`. Click one to
see its tags (`latest` and `sha-...`).

## Step 6: Make the packages public

New packages are private. Levels 8 to 10 pull these images onto a server and
a cluster, which is simpler when they are public. The images contain only
your configs and open source software, with no secrets, so public is fine
for this lab.

For each of the four packages: open it, click **Package settings** (right
side), scroll to **Danger zone**, click **Change visibility**, choose
**Public**, and type the package name to confirm.

## Step 7: Pull one of your images on the VM

Use your GitHub username in lowercase:

```bash
docker pull ghcr.io/YOUR-USERNAME/streamedge-edge:latest
docker images | grep streamedge-edge
```

That image was built by GitHub, not by you, and is now on your VM. If the
pull says "denied", the package is still private: repeat Step 6.

## Step 8: Add a status badge to your README (optional)

A badge shows the pipeline state on your repo page. Add this line at the top
of README.md, replacing YOUR-USERNAME:

```
![CI](https://github.com/YOUR-USERNAME/streamedge/actions/workflows/ci.yml/badge.svg)
```

---

# Experiments

## Experiment 1: Read the pipeline

On the Actions tab, open the latest run and look at the graph. Notice:
- "Check configs" ran first, and the other two stages waited for it.
- The four image builds ran in parallel.
- Click one build and read the "Work out the image tags" step. It shows the
  tags the image will get.

## Experiment 2: A broken config is caught before it ships

Use a branch and a pull request, as real teams do:

```bash
cd ~/streamedge
git checkout -b test-broken-config
echo "oops" >> origin/nginx.conf
git add .
git commit -m "test: broken nginx config"
git push -u origin test-broken-config
```

On github.com a yellow banner appears: **Compare & pull request**. Click it,
then **Create pull request**. The checks start. After a minute the
"Check configs" stage shows a **red X** at the NGINX step, and the test and
image stages never run. Nothing was published. The pull request page also
warns that checks failed.

Now fix it:

```bash
sed -i '$d' origin/nginx.conf
git add .
git commit -m "fix: remove broken line"
git push
```

The pull request checks run again and turn green. Merge the pull request
with the green button, or clean up without merging:

```bash
git checkout main
git branch -D test-broken-config
git push origin --delete test-broken-config
```

(Pull requests build the images but do not publish them. Only pushes to main
do.)

## Experiment 3: Make a release

Tags starting with `v` produce versioned images:

```bash
cd ~/streamedge
git checkout main
git pull
git tag v1.0.0
git push origin v1.0.0
```

Watch the new run on the Actions tab. When it finishes, open one package and
you will see a `v1.0.0` tag next to `latest` and `sha-...`. Pull that exact
version on the VM:

```bash
docker pull ghcr.io/YOUR-USERNAME/streamedge-edge:v1.0.0
```

A version number means "this exact build, forever". In Level 8 and 10 you
will deploy specific versions, not just `latest`.

## Experiment 4: Match an image to its code

```bash
git log --oneline -1
```

The short code at the start (for example `1a2b3c4`) matches the `sha-1a2b3c4`
tag of the newest image. From any running image you can trace back to the
exact commit that built it.

## Experiment 5: Build cache

Make a tiny change (for example a comment line in `edge/default.vcl`), push it
to main, and compare the build time of the second run with the first. The
images rebuild much faster because GitHub reuses cached layers.

---

# Save Level 7

The workflow is already in git from Step 4. Tag it:

```bash
cd ~/streamedge
git checkout main
git pull
git tag level-7
git push origin level-7
```

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
```

---

# If something goes wrong

- **Push rejected, mentions "workflow" scope:** your token cannot change
  workflow files. Fix the token permissions as described at the top, then push
  again. Alternative: create `.github/workflows/ci.yml` using the GitHub
  website (Add file, Create new file) and run `git pull` on the VM.
- **No run appears on the Actions tab:** the file must be at exactly
  `.github/workflows/ci.yml`, and the push must be to `main` (or a pull
  request). Check with `git ls-files .github`.
- **"Invalid workflow file" error on the Actions tab:** a YAML indentation
  mistake. The error names the line. Compare with the pasted block.
- **"Check configs" fails at the Varnish step:** open the step and read the
  message. A VCL error names the line. If the message is about the compiler or
  a missing file instead, paste it to me: that step may need a small change.
- **"Check configs" fails at the HAProxy step:** the message names the
  line of lb/haproxy.cfg.
- **"Run the system and test it" fails:** the last step prints container
  logs. Read the failing test's name: "segment is served and gets cached",
  "both edges report healthy" or "failover" tells you which behaviour broke.
- **"both edges report healthy" fails:** the test reads HAProxy's metric
  `haproxy_server_status` and counts the `state="UP"` lines that end in 1.
  Check the real names with `curl -s http://localhost:8404/metrics | grep haproxy_server_status`.
- **Failover test shows 404:** the test video must have several segments.
  The ffmpeg line in "Make a tiny test video" needs `-g 15 -keyint_min 15
  -sc_threshold 0` (a keyframe every second); without it the 12 second clip
  becomes one segment and `seg_001.ts` does not exist.
- **Publish fails with "denied" or "permission_denied":** in the repository
  open Settings, Actions, General, Workflow permissions, and choose "Read and
  write permissions". Then re-run the failed jobs.
- **Package not visible under your profile:** open the run, check the
  "Build (and push...)" step ran without errors; pull request runs never
  publish.
- **Package name has capital letters problem:** the workflow lowercases your
  username already. Use lowercase when you pull.
- **Re-run without changing anything:** on the run page click "Re-run all
  jobs".

# Next: Level 8

Ansible. A playbook that takes a brand new Ubuntu machine to a running
StreamEdge: install Docker, pull the images you just published, and start
everything. You will run it on your VM, and it can later point at any server.

