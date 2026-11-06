# StreamEdge - Level 8: Deploy with Ansible

Until now you started StreamEdge by typing commands on one machine. Level 8
writes those steps down as an **Ansible playbook**, so a brand new Ubuntu
machine becomes a running StreamEdge with one command, and running it again
is safe.

```
The pipeline roadmap:

  Level 6   Docker images
  Level 7   CI with GitHub Actions
  Level 8   Ansible                  <-- you are here
  Level 9   Kubernetes (k3s)         the same system as a cluster
  Level 10  GitOps with Argo CD      the cluster updates itself from git
  Level 11  Break it on purpose      failure experiments + postmortem
```

What one `ansible-playbook` run does:

```
ansible-playbook site.yml
   |
   v
 target machine (your VM now, any Ubuntu server later)
   1. install Docker
   2. create a service user and the folders
   3. copy the monitoring config and the exporter script
   4. run the exporter as a system service (starts at boot)
   5. pull YOUR images from GitHub Container Registry and start everything
   6. check the site answers through the load balancer
```

What changes compared with Levels 6 and 7:

| | Before | Level 8 |
|---|---|---|
| Images | Built on the VM | **Pulled from the registry** (the ones CI built and tested) |
| Where it lives | `~/streamedge` | `/opt/streamedge` |
| Exporter | `nohup python3 ...` by hand | A **system service**, starts at boot |
| After a reboot | You restart things | Everything comes back by itself |
| Setting up a new machine | Many manual steps | One command |
| Which version runs | Whatever you built last | Any version you name (`latest`, `v1.0.0`, `sha-...`) |

Words you will meet:
- **Inventory:** the list of machines to manage. Yours has one: this VM.
- **Playbook:** the file listing what the machine should look like.
- **Task / module:** one step, done by a module (`apt`, `copy`, `template`...).
- **Idempotent:** running it twice gives the same result. If everything is
  already right, nothing changes and Ansible reports `changed=0`.
- **Template:** a file with `{{ variables }}` filled in by Ansible.
- **Handler:** an action that runs only when something changed (for example,
  restart the exporter only if its script changed).
- **become:** run the task with sudo.

Files added in this level:

```
streamedge/
  ansible/
    ansible.cfg                     defaults for Ansible in this folder
    inventory.ini                   which machines: this VM
    group_vars/all.yml              settings: image owner, version, folder
    site.yml                        the playbook
    templates/
      docker-compose.yml.j2         compose file that uses the published images
      streamedge-exporter.service.j2   the exporter as a system service
```

Important: this level runs on the **same VM** as your old setup, so you first
stop the old one (they use the same ports). Your Grafana and Prometheus
history is kept.

---

# Steps (Ubuntu Server on VirtualBox)

Before you start, Level 7 must be working: all pipeline stages green and the
four packages published.

## Step 1: Save and tag Level 7 (skip if already done)

```bash
cd ~/streamedge
git add .
git commit -m "Level 7 done"
git tag level-7
git push
git push origin level-7
```

If git says "nothing to commit", run the `git tag` and `git push` lines only.

## Step 2: Check the VM can pull your published image

Replace `badyalrajesh` with your GitHub username in lowercase if different:

```bash
docker pull ghcr.io/badyalrajesh/streamedge-edge:latest
```

If it says `denied` or `unauthorized`, the package is still private. Make the
four packages public (Level 7, Step 6), or log in once as root:
`sudo docker login ghcr.io` with your GitHub username and a token that has
the `read:packages` permission.

## Step 3: Stop the old setup

```bash
cd ~/streamedge
docker compose down
pkill -f exporter.py
```

(`pkill` printing nothing is fine.) Your saved Grafana and Prometheus data
stays in Docker volumes.

## Step 4: Install Ansible

```bash
sudo apt update
sudo apt install -y ansible
ansible --version | head -1
```

You should see a version line.

## Step 5: Create the Ansible folder and files

```bash
cd ~/streamedge
mkdir -p ansible/templates ansible/group_vars
cd ansible
```

Ansible settings:

```bash
cat > ansible.cfg << 'EOF'
[defaults]
inventory = inventory.ini
interpreter_python = auto_silent
EOF
```

Inventory (the machines to manage):

```bash
cat > inventory.ini << 'EOF'
[streamedge]
localhost ansible_connection=local
EOF
```

Settings used by the playbook. **Check `ghcr_owner`**: it must be your GitHub
username in lowercase:

```bash
cat > group_vars/all.yml << 'EOF'
# Your GitHub username in lowercase (where the images were published)
ghcr_owner: badyalrajesh

# Which image version to deploy: latest, v1.0.0, sha-1a2b3c4 ...
image_tag: latest

# Where StreamEdge lives on the target machine
install_dir: /opt/streamedge

# The unprivileged user the watcher container runs as
streamedge_uid: 1500
streamedge_gid: 1500
EOF
```

The compose file template (the same stack as before, but it uses your
published images and restarts by itself after a reboot):

```bash
cat > templates/docker-compose.yml.j2 << 'EOF'
# Managed by Ansible. Do not edit by hand: change it in ansible/templates/
services:
  origin:
    image: ghcr.io/{{ ghcr_owner }}/streamedge-origin:{{ image_tag }}
    ports:
      - "8081:80"
    volumes:
      - {{ install_dir }}/video:/usr/share/nginx/html/video:ro
    restart: unless-stopped

  edge1:
    image: ghcr.io/{{ ghcr_owner }}/streamedge-edge:{{ image_tag }}
    ports:
      - "127.0.0.1:8082:80"
    depends_on:
      - origin
    restart: unless-stopped

  edge2:
    image: ghcr.io/{{ ghcr_owner }}/streamedge-edge:{{ image_tag }}
    ports:
      - "127.0.0.1:8083:80"
    depends_on:
      - origin
    restart: unless-stopped

  lb:
    image: ghcr.io/{{ ghcr_owner }}/streamedge-lb:{{ image_tag }}
    ports:
      - "8080:8080"
      - "8404:8404"
    depends_on:
      - edge1
      - edge2
    restart: unless-stopped

  watcher:
    image: ghcr.io/{{ ghcr_owner }}/streamedge-watcher:{{ image_tag }}
    user: "{{ streamedge_uid }}:{{ streamedge_gid }}"
    volumes:
      - {{ install_dir }}/incoming:/app/incoming
      - {{ install_dir }}/video:/app/origin/www/video
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
    restart: unless-stopped

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
    restart: unless-stopped

volumes:
  prom_data:
  grafana_data:
EOF
```

The exporter service file template:

```bash
cat > templates/streamedge-exporter.service.j2 << 'EOF'
# Managed by Ansible
[Unit]
Description=StreamEdge Prometheus exporter
After=docker.service
Requires=docker.service

[Service]
WorkingDirectory={{ install_dir }}
ExecStart=/usr/bin/python3 {{ install_dir }}/scripts/exporter.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

The playbook:

```bash
cat > site.yml << 'EOF'
---
- name: Deploy StreamEdge
  hosts: streamedge
  become: true

  tasks:
    # ---------- 1. The machine itself ----------
    - name: Install Docker and tools
      ansible.builtin.apt:
        name:
          - docker.io
          - docker-compose-v2
          - curl
        state: present
        update_cache: true
        cache_valid_time: 3600

    - name: Docker starts at boot and is running
      ansible.builtin.service:
        name: docker
        state: started
        enabled: true

    - name: Group for the StreamEdge services
      ansible.builtin.group:
        name: streamedge
        gid: "{{ streamedge_gid }}"
        state: present

    - name: User for the StreamEdge services
      ansible.builtin.user:
        name: streamedge
        uid: "{{ streamedge_uid }}"
        group: streamedge
        shell: /usr/sbin/nologin
        create_home: false
        state: present

    # ---------- 2. Folders and files ----------
    - name: Create the install folders
      ansible.builtin.file:
        path: "{{ item.path }}"
        state: directory
        owner: "{{ item.owner }}"
        group: "{{ item.owner }}"
        mode: "0755"
      loop:
        - { path: "{{ install_dir }}", owner: root }
        - { path: "{{ install_dir }}/scripts", owner: root }
        - { path: "{{ install_dir }}/incoming", owner: streamedge }
        - { path: "{{ install_dir }}/video", owner: streamedge }

    - name: Copy the Prometheus and Grafana config
      ansible.builtin.copy:
        src: "{{ playbook_dir }}/../monitoring/"
        dest: "{{ install_dir }}/monitoring/"
        owner: root
        group: root
        mode: "0644"
        directory_mode: "0755"

    - name: Copy the exporter script
      ansible.builtin.copy:
        src: "{{ playbook_dir }}/../scripts/exporter.py"
        dest: "{{ install_dir }}/scripts/exporter.py"
        owner: root
        group: root
        mode: "0755"
      notify: Restart exporter

    - name: Write docker-compose.yml
      ansible.builtin.template:
        src: docker-compose.yml.j2
        dest: "{{ install_dir }}/docker-compose.yml"
        owner: root
        group: root
        mode: "0644"

    # ---------- 3. The exporter as a system service ----------
    - name: Write the exporter service file
      ansible.builtin.template:
        src: streamedge-exporter.service.j2
        dest: /etc/systemd/system/streamedge-exporter.service
        owner: root
        group: root
        mode: "0644"
      notify:
        - Reload systemd
        - Restart exporter

    - name: Apply the pending service changes now
      ansible.builtin.meta: flush_handlers

    - name: Exporter starts at boot and is running
      ansible.builtin.systemd:
        name: streamedge-exporter
        enabled: true
        state: started

    # ---------- 4. The containers ----------
    - name: Pull the images and start the stack
      ansible.builtin.command:
        cmd: docker compose --progress plain up -d --pull always --remove-orphans
        chdir: "{{ install_dir }}"
      register: compose_up
      changed_when: (compose_up.stdout + compose_up.stderr) is search('Created|Started|Recreated')

    - name: Wait until the site answers through the load balancer
      ansible.builtin.uri:
        url: http://127.0.0.1:8080/
        status_code: 200
      register: site
      until: site.status == 200
      retries: 20
      delay: 3

    - name: Done
      ansible.builtin.debug:
        msg:
          - "Player:     http://localhost:8080"
          - "Grafana:    http://localhost:3000"
          - "HAProxy:    http://localhost:8404"
          - "Version:    {{ image_tag }}"
          - "Add videos: copy an .mp4 into {{ install_dir }}/incoming"

  handlers:
    - name: Reload systemd
      ansible.builtin.systemd:
        daemon_reload: true

    - name: Restart exporter
      ansible.builtin.systemd:
        name: streamedge-exporter
        state: restarted
EOF
```

Check everything is there:

```bash
find . -type f
```

You should see 6 files: ansible.cfg, inventory.ini, group_vars/all.yml, the
two templates and site.yml.

## Step 6: Check the playbook before running it

```bash
ansible-playbook site.yml --syntax-check
ansible streamedge -m ping
```

The first should print `playbook: site.yml`. The second should print `pong`.
(A "world writable directory" warning means the folder permissions are too
open: run `chmod 755 ~/streamedge/ansible` and try again.)

## Step 7: Run it

```bash
ansible-playbook site.yml -K
```

`-K` asks for your sudo password ("BECOME password"). The first run takes a
few minutes because it downloads the images.

Read the output as it goes. Each task prints `ok` (already right) or
`changed` (Ansible did something). At the end, the **PLAY RECAP** should show
`failed=0`. The last task prints the addresses for the player, Grafana and
HAProxy.

## Step 8: Check the result

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
systemctl status streamedge-exporter --no-pager | head -5
curl -s http://localhost:9100/metrics | head -4
curl -sI http://localhost:8080/ | head -1
```

You should see seven containers: five with `ghcr.io/...` images (origin, two
edges, lb, watcher) plus Prometheus and Grafana. The exporter should be
`active (running)`, the metrics should show `varnish_up` lines, and the last
command should print `HTTP/1.1 200 OK`.

Open http://localhost:8080 and http://localhost:3000 in your Windows
browser. The VirtualBox port rules you made earlier still apply. The player
has no videos yet: you add one in Experiment 3.

---

# Experiments

## Experiment 1: Run it again (idempotency)

```bash
cd ~/streamedge/ansible
ansible-playbook site.yml -K
```

The recap should show `changed=0` (or at most 1). Ansible checked every
task, found everything already correct, and changed nothing. That is what
makes it safe to run at any time.

## Experiment 2: Self-healing

Break two things on purpose:

```bash
sudo docker stop streamedge-edge1-1
sudo systemctl stop streamedge-exporter
```

Run the playbook again:

```bash
ansible-playbook site.yml -K
```

Watch the output: it restarts the edge container and the exporter, and
reports exactly those as `changed`. Check with `docker ps` and
`systemctl is-active streamedge-exporter`. The playbook describes the state
you want, so it repairs drift.

## Experiment 3: Add a video to the deployed system

```bash
ffmpeg -f lavfi -i testsrc2=duration=15:size=640x360:rate=30 \
       -f lavfi -i sine=frequency=550:duration=15 \
       -c:v libx264 -g 30 -pix_fmt yuv420p -c:a aac /tmp/movie1.mp4
sudo mv /tmp/movie1.mp4 /opt/streamedge/incoming/
sudo docker logs -f streamedge-watcher-1
```

You should see "converting", "published movie1". Press Ctrl+C. Reload the
player (Ctrl+F5): `movie1` is in the dropdown. (If `ffmpeg` is not installed
on the VM any more, use any small `.mp4` of your own.)

The folder `/opt/streamedge/incoming` belongs to the `streamedge` service
user, which is why `sudo` is used to put the file there.

## Experiment 4: Preview a change, then deploy a specific version

Open the Packages page on GitHub and copy a `sha-...` tag of one of your
images. Preview what Ansible would change, without changing anything:

```bash
ansible-playbook site.yml -K --check --diff -e image_tag=sha-1a2b3c4
```

(Use your real tag.) The output shows the lines in docker-compose.yml that
would change. Now apply it, and look at the images in use:

```bash
ansible-playbook site.yml -K -e image_tag=sha-1a2b3c4
docker ps --format 'table {{.Names}}\t{{.Image}}'
```

The containers are recreated with that exact version. Then go back:

```bash
ansible-playbook site.yml -K
```

This is how you deploy or roll back to any version that CI published.

## Experiment 5: Reboot test

```bash
sudo reboot
```

Wait a minute, log in again, and run:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
systemctl is-active streamedge-exporter
```

Everything is back with no commands from you, because Docker starts at
boot, the containers have `restart: unless-stopped`, and the exporter is a
system service. Open Grafana to confirm the dashboard is filling.

## Experiment 6 (optional): Deploy to a second machine

In VirtualBox, clone your VM (right-click it, Clone, choose "Generate new
MAC addresses"), start the clone, and make sure your first VM can reach it
over the network (for example, set both to Bridged Adapter). On the clone:
`sudo apt install -y openssh-server`. On the original VM:

```bash
ssh-keygen -t ed25519
ssh-copy-id YOUR-USER@CLONE-IP
```

Then add the clone to `ansible/inventory.ini`:

```
[streamedge]
newserver ansible_host=CLONE-IP ansible_user=YOUR-USER
```

Run the same playbook: `ansible-playbook site.yml -K`. The second machine
becomes a full StreamEdge with nothing installed by hand. (Remove the
`localhost` line if you want to deploy only to the clone.)

---

# Save Level 8 to GitHub

```bash
cd ~/streamedge
git add ansible
git commit -m "Level 8: Ansible playbook to deploy StreamEdge"
git tag level-8
git push
git push origin level-8
git ls-files ansible
```

`git ls-files` should list the six Ansible files. Never put a password or a
token in these files. If you ever need one, pass it on the command line
with `-e` or use Ansible Vault.

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
```

---

# Going back to the local setup

```bash
sudo systemctl stop streamedge-exporter
cd /opt/streamedge && sudo docker compose down
cd ~/streamedge && docker compose up -d
nohup python3 scripts/exporter.py > exporter.log 2>&1 &
```

---

# If something goes wrong

- `ansible: command not found`: repeat Step 4.
- `Missing sudo password` or `sudo: a password is required`: add `-K` to the
  command.
- "world writable directory" warning, settings ignored: run
  `chmod 755 ~/streamedge/ansible`.
- A task fails with `Unable to find a package docker-compose-v2`: your Ubuntu
  version may be older. Run `lsb_release -a` and paste me the result.
- `port is already allocated`: the old setup is still running. Run
  `docker ps` and `cd ~/streamedge && docker compose down`. If port 9100 is
  busy, run `pkill -f exporter.py` and rerun the playbook.
- `denied`, `unauthorized` or `manifest unknown` while pulling images: the
  packages are private (see Step 2), `ghcr_owner` in `group_vars/all.yml` is
  wrong, or the `image_tag` does not exist on the Packages page.
- The last task, "Wait until the site answers", fails: check
  `sudo docker compose -f /opt/streamedge/docker-compose.yml ps` and look at
  `sudo docker compose -f /opt/streamedge/docker-compose.yml logs lb`.
- Exporter service fails: `sudo journalctl -u streamedge-exporter -n 20`.
- The playbook says `changed` every run on the "Pull the images" task: paste
  me the line it prints in the output so we can adjust the change detection.
- YAML error naming a line: an indentation mistake in the pasted file.
  `ansible-playbook site.yml --syntax-check` shows the line.
- Grafana shows no data after the move: wait a minute, and check
  `curl -s http://localhost:9100/metrics | head` still works.

# Next: Level 9

Kubernetes with k3s. The same system as a cluster: Deployments, Services,
ConfigMaps, health checks, and scaling the edges from 2 to 3. The exporter
becomes a small container that runs next to each edge.

