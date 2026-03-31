#!/usr/bin/env python3
"""
Kali Docker Web Manager — Flask Web Interface
VirtualBox-style UI for managing Kali Linux Docker sessions
"""

import os
import json
import time
import socket
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from functools import wraps

try:
    import docker
except ImportError:
    subprocess.call(["pip", "install", "docker", "--break-system-packages", "-q"])
    import docker

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE        = Path.home() / ".kali_sessions.json"
DATA_DIR           = Path.home() / "kali_sessions"
DOCKER_IMAGE       = "lscr.io/linuxserver/kali-linux:latest"
CONTAINER_PORT     = "3000/tcp"
CONTAINER_PORT_ALT = "3001/tcp"
PORT_START         = 6081
PORT_ALT_OFFSET    = 1000
CPU_LIMIT          = 8
MEM_LIMIT          = "8g"
MEM_SWAP           = "10g"
VNC_PASSWORD       = "kaliadmin777"
PUID               = 1000
PGID               = 1000

app = Flask(__name__)
app.secret_key = os.urandom(32)

# ── Auth Config ───────────────────────────────────────────────────────────────
ADMIN_USERNAME = "administrator"
ADMIN_PASSWORD = "changeme"
WHITELIST_FILE = Path("whitelist.txt")

def load_whitelist():
    """Load allowed IPs from whitelist.txt (one IP per line, # for comments)."""
    if not WHITELIST_FILE.exists():
        return []
    ips = []
    for line in WHITELIST_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ips.append(line)
    return ips

def get_client_ip():
    """Get real client IP (handles reverse proxy X-Forwarded-For)."""
    if request.headers.get("X-Forwarded-For"):
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

def is_ip_allowed():
    """Return True if whitelist is empty (open) or client IP is in it."""
    whitelist = load_whitelist()
    if not whitelist:
        return True
    return get_client_ip() in whitelist

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_ip_allowed():
            return render_template("blocked.html", ip=get_client_ip()), 403
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def api_auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_ip_allowed():
            return jsonify({"error": "Access denied — IP not whitelisted"}), 403
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"sessions": {}, "next_port": PORT_START}

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def get_docker_client():
    try:
        client = docker.from_env()
        client.ping()
        return client, None
    except Exception as e:
        return None, str(e)

def container_name(username):
    return f"kali_session_{username}"

def user_data_dir(username):
    p = DATA_DIR / username
    p.mkdir(parents=True, exist_ok=True)
    return p

def get_container_status(client, username):
    try:
        c = client.containers.get(container_name(username))
        return c.status
    except:
        return "missing"

def get_container_stats(client, username):
    try:
        c = client.containers.get(container_name(username))
        if c.status != "running":
            return {}
        stats = c.stats(stream=False)
        cpu_d = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                stats["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_d = stats["cpu_stats"].get("system_cpu_usage", 0) - \
                stats["precpu_stats"].get("system_cpu_usage", 1)
        ncpu = stats["cpu_stats"].get("online_cpus", 1)
        cpu_pct = (cpu_d / sys_d) * ncpu * 100 if sys_d else 0
        mem_u = stats["memory_stats"].get("usage", 0)
        mem_l = stats["memory_stats"].get("limit", 1)
        mem_pct = mem_u / mem_l * 100
        net_rx = stats.get("networks", {})
        rx = sum(v.get("rx_bytes", 0) for v in net_rx.values())
        tx = sum(v.get("tx_bytes", 0) for v in net_rx.values())
        return {
            "cpu": round(cpu_pct, 1),
            "mem_used": mem_u // 1024 // 1024,
            "mem_total": mem_l // 1024 // 1024,
            "mem_pct": round(mem_pct, 1),
            "net_rx": rx // 1024,
            "net_tx": tx // 1024,
        }
    except:
        return {}

def _disable_screen_lock(client, cname):
    cmds = [
        "bash -c 'pkill -f xfce4-screensaver 2>/dev/null; true'",
        "bash -c 'pkill -f xscreensaver 2>/dev/null; true'",
        "bash -c 'pkill -f light-locker 2>/dev/null; true'",
        "bash -c 'DISPLAY=:1 xset s off -dpms 2>/dev/null; true'",
    ]
    try:
        c = client.containers.get(cname)
        for cmd in cmds:
            c.exec_run(cmd, user="root", detach=True)
    except:
        pass

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if not is_ip_allowed():
        return render_template("blocked.html", ip=get_client_ip()), 403
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("index"))
        else:
            error = "Invalid username or password"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/api/status")
@api_auth_required
def api_status():
    client, err = get_docker_client()
    if err:
        return jsonify({"docker": False, "error": err})
    try:
        info = client.info()
        return jsonify({
            "docker": True,
            "host_ip": get_local_ip(),
            "docker_version": info.get("ServerVersion", "unknown"),
            "containers_running": info.get("ContainersRunning", 0),
            "containers_total": info.get("Containers", 0),
        })
    except Exception as e:
        return jsonify({"docker": False, "error": str(e)})

@app.route("/api/sessions")
@api_auth_required
def api_sessions():
    client, err = get_docker_client()
    cfg = load_config()
    host_ip = get_local_ip()
    sessions_out = []
    for uname, s in cfg.get("sessions", {}).items():
        status = get_container_status(client, uname) if client else "unknown"
        port = s.get("port", "—")
        port_alt = s.get("port_alt", "—")
        created = s.get("created_at", "")[:16].replace("T", " ")
        sessions_out.append({
            "username": uname,
            "status": status,
            "port": port,
            "port_alt": port_alt,
            "password": s.get("password", "—"),
            "url": f"https://{host_ip}:{port}",
            "url_alt": f"https://{host_ip}:{port_alt}",
            "created_at": created,
            "data_dir": s.get("data_dir", ""),
            "mem_limit": MEM_LIMIT,
            "cpu_limit": CPU_LIMIT,
        })
    return jsonify({"sessions": sessions_out, "host_ip": host_ip})

@app.route("/api/sessions/<username>/stats")
@api_auth_required
def api_session_stats(username):
    client, err = get_docker_client()
    if err:
        return jsonify({"error": err}), 500
    return jsonify(get_container_stats(client, username))

@app.route("/api/sessions/<username>/logs")
@api_auth_required
def api_session_logs(username):
    client, err = get_docker_client()
    if err:
        return jsonify({"error": err}), 500
    tail = int(request.args.get("tail", 80))
    try:
        c = client.containers.get(container_name(username))
        logs = c.logs(tail=tail).decode(errors="replace")
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@app.route("/api/sessions", methods=["POST"])
@api_auth_required
def api_create_session():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", VNC_PASSWORD).strip() or VNC_PASSWORD
    if not username:
        return jsonify({"error": "Username is required"}), 400

    client, err = get_docker_client()
    if err:
        return jsonify({"error": f"Docker error: {err}"}), 500

    cfg = load_config()
    cname = container_name(username)
    try:
        client.containers.get(cname)
        return jsonify({"error": f"Session '{username}' already exists"}), 409
    except docker.errors.NotFound:
        pass

    port = cfg["next_port"]
    port_alt = port + PORT_ALT_OFFSET
    cfg["next_port"] += 1
    data_dir = user_data_dir(username)
    host_ip = get_local_ip()

    session = {
        "username": username, "password": password,
        "port": port, "port_alt": port_alt,
        "container": cname, "data_dir": str(data_dir),
        "created_at": datetime.now().isoformat(), "status": "creating",
    }
    cfg["sessions"][username] = session
    save_config(cfg)

    def create_bg():
        try:
            client.containers.run(
                DOCKER_IMAGE, detach=True, name=cname,
                hostname=f"kali-{username}",
                environment={
                    "CUSTOM_USER": username, "PASSWORD": password,
                    "VNCPASSWORD": password, "VNC_PW": password,
                    "RESOLUTION": "1280x768", "PUID": str(PUID),
                    "PGID": str(PGID), "TZ": "Etc/UTC",
                    "DISABLE_LOCK": "true", "NO_SCREEN_LOCK": "true",
                },
                ports={
                    CONTAINER_PORT: ("0.0.0.0", port),
                    CONTAINER_PORT_ALT: ("0.0.0.0", port_alt),
                },
                volumes={str(data_dir): {"bind": "/home/kali", "mode": "rw"}},
                shm_size="512m", nano_cpus=int(CPU_LIMIT * 1e9),
                mem_limit=MEM_LIMIT, memswap_limit=MEM_SWAP,
                restart_policy={"Name": "unless-stopped"},
                cap_add=["SYS_PTRACE"], security_opt=["seccomp=unconfined"],
            )
            time.sleep(5)
            _disable_screen_lock(client, cname)
        except Exception:
            c2 = load_config()
            c2["sessions"].pop(username, None)
            save_config(c2)

    threading.Thread(target=create_bg, daemon=True).start()
    return jsonify({
        "message": f"Session '{username}' is being created",
        "username": username, "port": port, "port_alt": port_alt,
        "url": f"https://{host_ip}:{port}",
    }), 202

@app.route("/api/sessions/<username>/stop", methods=["POST"])
@api_auth_required
def api_stop_session(username):
    client, err = get_docker_client()
    if err:
        return jsonify({"error": err}), 500
    cfg = load_config()
    try:
        c = client.containers.get(container_name(username))
        c.stop(timeout=10)
        if username in cfg["sessions"]:
            cfg["sessions"][username]["status"] = "stopped"
        save_config(cfg)
        return jsonify({"message": f"Session '{username}' stopped"})
    except docker.errors.NotFound:
        return jsonify({"error": "Container not found"}), 404

@app.route("/api/sessions/<username>/start", methods=["POST"])
@api_auth_required
def api_start_session(username):
    client, err = get_docker_client()
    if err:
        return jsonify({"error": err}), 500
    cfg = load_config()
    try:
        c = client.containers.get(container_name(username))
        c.start()
        if username in cfg["sessions"]:
            cfg["sessions"][username]["status"] = "running"
        save_config(cfg)
        time.sleep(3)
        _disable_screen_lock(client, container_name(username))
        return jsonify({"message": f"Session '{username}' started"})
    except docker.errors.NotFound:
        return jsonify({"error": "Container not found"}), 404

@app.route("/api/sessions/<username>/restart", methods=["POST"])
@api_auth_required
def api_restart_session(username):
    client, err = get_docker_client()
    if err:
        return jsonify({"error": err}), 500
    try:
        c = client.containers.get(container_name(username))
        c.restart(timeout=10)
        time.sleep(4)
        _disable_screen_lock(client, container_name(username))
        return jsonify({"message": f"Session '{username}' restarted"})
    except docker.errors.NotFound:
        return jsonify({"error": "Container not found"}), 404

@app.route("/api/sessions/<username>", methods=["DELETE"])
@api_auth_required
def api_delete_session(username):
    client, err = get_docker_client()
    if err:
        return jsonify({"error": err}), 500
    cfg = load_config()
    try:
        c = client.containers.get(container_name(username))
        c.remove(force=True)
    except:
        pass
    cfg["sessions"].pop(username, None)
    save_config(cfg)
    return jsonify({"message": f"Session '{username}' removed"})

@app.route("/api/sessions/<username>/password", methods=["POST"])
@api_auth_required
def api_change_password(username):
    data = request.json or {}
    new_pw = data.get("password", "").strip()
    if not new_pw:
        return jsonify({"error": "Password required"}), 400
    client, err = get_docker_client()
    if err:
        return jsonify({"error": err}), 500
    cfg = load_config()
    try:
        c = client.containers.get(container_name(username))
        c.exec_run(f"bash -c \"echo '{username}:{new_pw}' | chpasswd 2>/dev/null; true\"")
        if username in cfg["sessions"]:
            cfg["sessions"][username]["password"] = new_pw
        save_config(cfg)
        return jsonify({"message": f"Password updated. Restart container for full effect."})
    except docker.errors.NotFound:
        return jsonify({"error": "Container not found"}), 404

@app.route("/api/sessions/<username>/exec", methods=["POST"])
@api_auth_required
def api_exec_session(username):
    data = request.json or {}
    cmd = data.get("command", "").strip()
    if not cmd:
        return jsonify({"error": "Command required"}), 400
    client, err = get_docker_client()
    if err:
        return jsonify({"error": err}), 500
    try:
        c = client.containers.get(container_name(username))
        exit_code, output = c.exec_run(cmd, stream=False, tty=False)
        out = output.decode(errors="replace") if output else ""
        return jsonify({"output": out, "exit_code": exit_code})
    except docker.errors.NotFound:
        return jsonify({"error": "Container not found"}), 404

@app.route("/api/bulk_create", methods=["POST"])
@api_auth_required
def api_bulk_create():
    data = request.json or {}
    prefix = data.get("prefix", "student").strip()
    count = int(data.get("count", 1))
    password = data.get("password", VNC_PASSWORD).strip() or VNC_PASSWORD
    if count < 1 or count > 50:
        return jsonify({"error": "Count must be between 1 and 50"}), 400
    client, err = get_docker_client()
    if err:
        return jsonify({"error": err}), 500
    cfg = load_config()

    def bulk_bg():
        for i in range(1, count + 1):
            uname = f"{prefix}{i:02d}"
            cname = container_name(uname)
            try:
                client.containers.get(cname)
                continue
            except docker.errors.NotFound:
                pass
            port = cfg["next_port"]
            port_alt = port + PORT_ALT_OFFSET
            cfg["next_port"] += 1
            data_dir = user_data_dir(uname)
            cfg["sessions"][uname] = {
                "username": uname, "password": password,
                "port": port, "port_alt": port_alt,
                "container": cname, "data_dir": str(data_dir),
                "created_at": datetime.now().isoformat(), "status": "creating",
            }
            save_config(cfg)
            try:
                client.containers.run(
                    DOCKER_IMAGE, detach=True, name=cname,
                    hostname=f"kali-{uname}",
                    environment={
                        "CUSTOM_USER": uname, "PASSWORD": password,
                        "VNCPASSWORD": password, "VNC_PW": password,
                        "RESOLUTION": "1280x768", "PUID": str(PUID),
                        "PGID": str(PGID), "TZ": "Etc/UTC",
                        "DISABLE_LOCK": "true", "NO_SCREEN_LOCK": "true",
                    },
                    ports={
                        CONTAINER_PORT: ("0.0.0.0", port),
                        CONTAINER_PORT_ALT: ("0.0.0.0", port_alt),
                    },
                    volumes={str(data_dir): {"bind": "/home/kali", "mode": "rw"}},
                    shm_size="512m", nano_cpus=int(CPU_LIMIT * 1e9),
                    mem_limit=MEM_LIMIT, memswap_limit=MEM_SWAP,
                    restart_policy={"Name": "unless-stopped"},
                    cap_add=["SYS_PTRACE"], security_opt=["seccomp=unconfined"],
                )
                time.sleep(2)
                _disable_screen_lock(client, cname)
            except:
                cfg["sessions"].pop(uname, None)
                save_config(cfg)

    threading.Thread(target=bulk_bg, daemon=True).start()
    return jsonify({"message": f"Creating {count} sessions with prefix '{prefix}'"}), 202

@app.route("/api/pull_image", methods=["POST"])
@api_auth_required
def api_pull_image():
    client, err = get_docker_client()
    if err:
        return jsonify({"error": err}), 500
    def pull_bg():
        try:
            for _ in client.api.pull(DOCKER_IMAGE, stream=True, decode=True):
                pass
        except:
            pass
    threading.Thread(target=pull_bg, daemon=True).start()
    return jsonify({"message": f"Pulling {DOCKER_IMAGE} in background..."})

if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("\n  Kali Docker Web Manager")
    print("  Open: http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
