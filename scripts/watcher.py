#!/usr/bin/env python3
"""StreamEdge watcher: converts new .mp4 files in incoming/ to HLS."""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCOMING = ROOT / "incoming"
DONE = INCOMING / "done"
FAILED = INCOMING / "failed"
VIDEO_DIR = ROOT / "origin" / "www" / "video"
VIDEOS_JSON = ROOT / "origin" / "www" / "videos.json"
POLL_SECONDS = 2


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def safe_name(stem):
    return re.sub(r"[^A-Za-z0-9_-]", "-", stem)


def update_index():
    """Write videos.json: the list of finished videos shown in the player."""
    names = sorted(
        p.parent.name
        for p in VIDEO_DIR.glob("*/index.m3u8")
        if not p.parent.name.startswith(".")
    )
    tmp = VIDEOS_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(names))
    tmp.replace(VIDEOS_JSON)
    log("videos.json now lists: " + ", ".join(names))


def convert(src):
    name = safe_name(src.stem)
    tmp_dir = VIDEO_DIR / (".tmp-" + name)
    final_dir = VIDEO_DIR / name

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    cmd = [
        "ffmpeg", "-loglevel", "error",
        "-i", str(src),
        "-c:v", "libx264", "-c:a", "aac",
        "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod",
        "-hls_segment_filename", str(tmp_dir / "seg_%03d.ts"),
        str(tmp_dir / "index.m3u8"),
    ]
    log("converting " + src.name + " -> video/" + name + "/")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log("ffmpeg failed: " + result.stderr.strip()[-300:])
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False

    # Publish in one step: the video appears only when it is complete
    replaced = final_dir.exists()
    if replaced:
        shutil.rmtree(final_dir)
    tmp_dir.rename(final_dir)
    if replaced:
        log("NOTE: '" + name + "' was replaced. Edges may still hold the old "
            "copy. Run: python3 scripts/purge.py " + name)
    log("published " + name)
    return True


def main():
    for d in (INCOMING, DONE, FAILED, VIDEO_DIR):
        d.mkdir(parents=True, exist_ok=True)
    update_index()
    log("watching " + str(INCOMING) + "  (Ctrl+C to stop)")

    last_size = {}
    while True:
        for f in sorted(INCOMING.glob("*.mp4")):
            try:
                size = f.stat().st_size
            except FileNotFoundError:
                continue
            # Only convert when the size stopped changing (upload finished)
            if size > 0 and last_size.get(f) == size:
                ok = convert(f)
                shutil.move(str(f), str((DONE if ok else FAILED) / f.name))
                last_size.pop(f, None)
                if ok:
                    update_index()
            else:
                last_size[f] = size
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped")
