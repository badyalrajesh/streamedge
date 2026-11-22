# StreamEdge on Kubernetes: daily operations

A short runbook for the k3s cluster on the Ubuntu VM: videos, stopping,
starting, deleting and deploying again.

Run all commands on the VM, in `~/streamedge`. One-time setup per login
(already in `~/.bashrc` if you followed Level 9, Step 4):

```bash
export KUBECONFIG=$HOME/.kube/config
```

Everything lives in the namespace `streamedge`, so the commands below spell
it out with `-n streamedge`.

---

## 1. Check the state

```bash
kubectl get pods -n streamedge          # all should be Running (edges 2/2)
kubectl get svc -n streamedge           # lb, grafana, prometheus have ports
curl -s http://localhost:8080/video/videos.json    # videos the player lists
```

Windows browser: player http://localhost:8080, Grafana http://localhost:3000,
HAProxy http://localhost:8404.

---

## 2. Upload a video

Why not just `cp`: the watcher runs inside a container and cannot see the
VM's folders. The file has to be copied into the cluster.

**One-time setup, save the helper script:**

```bash
cat > ~/upload.sh << 'EOF'
#!/bin/bash
# Usage: ./upload.sh /path/to/clip.mp4
set -e
f="$1"
[ -f "$f" ] || { echo "file not found: $f"; exit 1; }
n=$(basename "$f" | tr -c '[:alnum:]._\n' '-')
pod=$(kubectl get pod -l app=watcher -n streamedge -o name | cut -d/ -f2)
kubectl cp "$f" "streamedge/$pod:/app/incoming/$n.part"
kubectl exec -n streamedge deploy/watcher -- mv "/app/incoming/$n.part" "/app/incoming/$n"
echo "sent $n - watch it with: kubectl logs -f deploy/watcher -n streamedge"
EOF
chmod +x ~/upload.sh
```

**Every time:**

```bash
~/upload.sh ~/Downloads/YOUR-FILE.mp4
kubectl logs -f deploy/watcher -n streamedge     # wait for "published NAME", then Ctrl+C
curl -s http://localhost:8080/video/videos.json  # NAME should be listed
```

Then press Ctrl+F5 on the player and pick the video. Rules: the file name
must end in lowercase `.mp4`, and use a new name each time (a reused name
replaces the video, and the edges keep serving the old copy until you purge).

---

## 3. Delete a video

**One-time setup, save the helper script:**

```bash
cat > ~/delete-video.sh << 'EOF'
#!/bin/bash
# Usage: ./delete-video.sh VIDEO_NAME   (the name shown in the player dropdown)
set -e
name="$1"
if [[ ! "$name" =~ ^[A-Za-z0-9_-][A-Za-z0-9._-]*$ ]]; then
  echo "usage: ./delete-video.sh VIDEO_NAME"
  exit 1
fi
kubectl exec -n streamedge deploy/watcher -- rm -rf "/app/origin/www/video/$name" "/app/incoming/done/$name.mp4"
kubectl rollout restart deployment/watcher -n streamedge
kubectl rollout restart deployment/edge -n streamedge
kubectl rollout status deployment/watcher -n streamedge
echo "deleted $name - press Ctrl+F5 on the player"
EOF
chmod +x ~/delete-video.sh
```

**Every time:**

```bash
~/delete-video.sh VIDEO_NAME
curl -s http://localhost:8080/video/videos.json    # NAME should be gone
```

What it does: removes the video files and the saved original, restarts the
watcher (so it rebuilds the video list), and restarts the edges (so they drop
their cached copy). Restarting the edges clears the cache for every video,
which refills as people watch.

---

## 4. Stop and start

| Goal | Stop | Start again |
|---|---|---|
| Stop the pods, keep the cluster | `kubectl scale deployment --all --replicas=0 -n streamedge` | `cd ~/streamedge && kubectl apply -k k8s` |
| Stop the whole cluster (frees all memory and ports) | `sudo /usr/local/bin/k3s-killall.sh` | `sudo systemctl start k3s`, then `kubectl get pods -A` |
| Restart one service | `kubectl rollout restart deployment/edge -n streamedge` | (it restarts itself) |
| Change the number of edges | `kubectl scale deployment edge --replicas=3 -n streamedge` | `kubectl scale deployment edge --replicas=2 -n streamedge` |

Notes:
- `kubectl apply -k k8s` sets every service back to the number of copies in
  your files (2 edges, 1 of everything else), so it is also the "start
  everything" command after scaling to zero.
- After `k3s-killall.sh`, pods come back on their own when k3s starts.
  Allow 1 to 2 minutes.
- After a VM reboot, k3s starts by itself and the pods return. Check with
  `kubectl get pods -n streamedge`. To stop k3s from starting at boot:
  `sudo systemctl disable k3s`.

---

## 5. Delete pods

A plain pod delete is not permanent: the Deployment starts a new one.

| Goal | Command | Videos kept? |
|---|---|---|
| Replace one broken pod | `kubectl delete pod POD-NAME -n streamedge` | Yes |
| Delete all apps, keep the data | `kubectl delete deployment --all -n streamedge` | **Yes** |
| Delete everything including data | `kubectl delete -k k8s` | **No**, videos and Prometheus history are deleted |
| Remove k3s completely | `sudo /usr/local/bin/k3s-uninstall.sh` | No |

Get a pod's name with `kubectl get pods -n streamedge`. Pod names change
every time a pod is replaced.

---

## 6. Deploy again next time

Start from the situation you are in:

| Situation | What to do |
|---|---|
| Pods scaled to zero | `cd ~/streamedge && kubectl apply -k k8s` |
| After `k3s-killall.sh` or a VM reboot | `sudo systemctl start k3s` (if it is not running), wait 2 minutes, then `kubectl get pods -n streamedge` |
| After `kubectl delete deployment --all` | `cd ~/streamedge && kubectl apply -k k8s` (your videos are still there) |
| After `kubectl delete -k k8s` | `cd ~/streamedge && kubectl apply -k k8s`, then upload videos again (section 2) |
| After removing k3s completely | Level 9, Step 4 (install k3s), then Steps 5 to 9 |
| Deploy a new version of an image | Wait for the CI run to turn green, then `kubectl rollout restart deployment/NAME -n streamedge` |

After any start, check:

```bash
kubectl get pods -n streamedge
curl -sI http://localhost:8080/ | head -1
```

Pods `Running` and `HTTP/1.1 200 OK` mean it is up.

---

## 7. Quick problem checks

| Symptom | Check |
|---|---|
| A pod is not `Running` | `kubectl describe pod POD-NAME -n streamedge`, read the last lines |
| Upload does nothing | `kubectl logs deploy/watcher -n streamedge` |
| Video still shows after delete | Compare `curl` of `videos.json` with `kubectl exec deploy/origin -n streamedge -- ls /usr/share/nginx/html/video`, then restart the edges |
| `connection refused` from kubectl | `export KUBECONFIG=$HOME/.kube/config`, and `sudo systemctl status k3s` |
| 404 right after an upload | The edges cache a "not found" for about 2 minutes. Wait, or restart the edges |

