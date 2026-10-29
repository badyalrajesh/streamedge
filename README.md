# StreamEdge - Step 1: Origin server with HLS video

A mini CDN practice project. One NGINX origin serves a video that has been
cut into small HLS pieces. You watch it in a browser.

```
streamedge/
  docker-compose.yml     starts NGINX on port 8080
  .gitignore             keeps big video files out of git
  incoming/              source video goes here (not committed)
  origin/
    nginx.conf           short cache for .m3u8, long cache for .ts
    www/
      index.html         hls.js player page
      video/             generated HLS files (not committed)
```

What you learn: HLS = a manifest (.m3u8) listing small segments (.ts).
Manifests change, so cache briefly. Segments never change, so cache long.

---

# Steps from scratch (Ubuntu Server on VirtualBox)

Start with Ubuntu running and you logged in. Copy and paste each block into
the Ubuntu terminal, one step at a time. Replace YOUR-USERNAME and
you@example.com where shown.

## Step 1: Update Ubuntu and install tools

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 ffmpeg git curl
```

Check:

```bash
docker --version
docker compose version
ffmpeg -version | head -1
git --version
```

All four should print a version.

## Step 2: Let your user run Docker without sudo

```bash
sudo usermod -aG docker $USER
newgrp docker
```

Check (should print "Hello from Docker!"):

```bash
docker run --rm hello-world
```

## Step 3: Create the project folders

```bash
mkdir -p ~/streamedge/origin/www/video ~/streamedge/incoming
cd ~/streamedge
touch incoming/.gitkeep origin/www/video/.gitkeep
```

## Step 4: Create the files

Run each block as one paste. Each one creates a file.

docker-compose.yml:

```bash
cat > docker-compose.yml << 'EOF'
services:
  origin:
    image: nginx:1.27-alpine
    ports:
      - "8080:80"
    volumes:
      - ./origin/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./origin/www:/usr/share/nginx/html:ro
EOF
```

nginx.conf:

```bash
cat > origin/nginx.conf << 'EOF'
server {
    listen 80;
    root /usr/share/nginx/html;

    location / {
        add_header Access-Control-Allow-Origin *;
    }

    # Manifest changes, so short cache life
    location ~ \.m3u8$ {
        add_header Cache-Control "public, max-age=2";
        add_header Access-Control-Allow-Origin *;
    }

    # Segments never change, so long cache life
    location ~ \.ts$ {
        add_header Cache-Control "public, max-age=3600";
        add_header Access-Control-Allow-Origin *;
    }
}
EOF
```

index.html:

```bash
cat > origin/www/index.html << 'EOF'
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>StreamEdge</title></head>
<body>
  <h1>StreamEdge</h1>
  <video id="v" controls width="640"></video>
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
  </script>
</body>
</html>
EOF
```

.gitignore:

```bash
cat > .gitignore << 'EOF'
# Large generated media: do not commit
incoming/*
!incoming/.gitkeep
origin/www/video/*
!origin/www/video/.gitkeep
EOF
```

Check:

```bash
find . -type f
```

You should see the 4 files above plus the 2 .gitkeep files.

## Step 5: Make a test video

This generates a 20 second video with sound, so you don't need to upload one.

```bash
ffmpeg -f lavfi -i testsrc=duration=20:size=640x360:rate=30 \
       -f lavfi -i sine=frequency=440:duration=20 \
       -c:v libx264 -pix_fmt yuv420p -c:a aac incoming/sample.mp4
```

(To use your own video instead, copy it to `incoming/sample.mp4`.)

## Step 6: Convert it to HLS

```bash
ffmpeg -i incoming/sample.mp4 -c:v libx264 -c:a aac -f hls \
       -hls_time 4 -hls_playlist_type vod \
       -hls_segment_filename origin/www/video/seg_%03d.ts \
       origin/www/video/index.m3u8
ls origin/www/video
```

You should see `index.m3u8` and several `seg_000.ts`, `seg_001.ts`, ... files.

## Step 7: Start the origin server

```bash
docker compose up -d
docker compose ps
```

The `origin` service should show as running.

## Step 8: Test inside Ubuntu

```bash
curl -I http://localhost:8080/video/index.m3u8
curl -I http://localhost:8080/video/seg_000.ts
```

Both should say `200 OK`. Look at the `Cache-Control` line:
`max-age=2` for the manifest, `max-age=3600` for the segment.
That is your first real CDN concept working.

## Step 9: Watch it from your Windows browser

Ubuntu Server has no screen browser, so forward a port in VirtualBox:

1. In VirtualBox, select your Ubuntu VM, open Settings, then Network.
2. Adapter 1 (Attached to: NAT), click Advanced, then Port Forwarding.
3. Add a rule: Protocol TCP, Host Port 8080, Guest Port 8080
   (leave the IP fields empty). Click OK.

Now on Windows open http://localhost:8080 and press play.

(Alternative: set the adapter to Bridged, run `ip a` in Ubuntu to get its
IP address, and open http://THAT-IP:8080 from Windows.)

## Step 10: Save the project to GitHub

One-time git setup:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
git config --global init.defaultBranch main
```

Make a GitHub token (GitHub no longer accepts your password for pushes):

1. On github.com, create a new empty repository named `streamedge`
   (no README, no .gitignore).
2. Go to Settings, Developer settings, Personal access tokens, Fine-grained
   tokens, Generate new token. Give it access to the `streamedge` repo with
   Contents: Read and write. Copy the token.

Commit and push:

```bash
cd ~/streamedge
git init
git add .
git commit -m "StreamEdge step 1: NGINX origin with HLS playback"
git remote add origin https://github.com/YOUR-USERNAME/streamedge.git
git push -u origin main
```

When asked, Username is your GitHub username and Password is the token.

Check what was committed (the video files should NOT be listed):

```bash
git ls-files
```

Refresh the repo page on GitHub to see your files.

## Everyday commands

```bash
cd ~/streamedge
docker compose up -d       # start
docker compose down        # stop
docker compose logs        # see logs
```

After the next steps, save your work with:

```bash
git add .
git commit -m "describe what you changed"
git push
```

## If something goes wrong

- `permission denied` on docker: repeat Step 2, or log out and back in.
- `docker compose` not found: run `sudo apt install -y docker-compose-v2`.
- Port 8080 busy: change `"8080:80"` in docker-compose.yml and the VirtualBox rule.
- Page loads but video won't play: run `ls origin/www/video` and make sure the .m3u8 and .ts files exist.
- Windows browser can't connect: recheck the Step 9 port forwarding rule.
