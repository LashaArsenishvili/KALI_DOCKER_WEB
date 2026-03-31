#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          KALI LINUX DOCKER SESSION MANAGER  v2.2 (fixed)                   ║
║          Each user gets an isolated Kali desktop (XFCE + KasmVNC)          ║
║          Storage limit: 10GB per container                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

  FIXES in v2.2:
    - Port 3000 is HTTPS on linuxserver/kali-linux (not plain HTTP).
      All URLs now use https:// — works from any device (accept self-signed cert).
    - Removed misleading "HTTP port" concept; both ports now correctly labelled.
    - VNC login: username = kali  |  password = whatever you set (default: kali)

  Requirements:
    pip install docker rich blessed inquirer tabulate requests --break-system-packages

  Docker image used: lscr.io/linuxserver/kali-linux:latest
    - Comes with XFCE desktop + KasmVNC (browser-based VNC over HTTPS)
    - Port 3000 → HTTPS desktop  (accept self-signed cert warning)
    - Port 3001 → HTTPS alternate / redirect

  Quick start:
    python3 kali_docker_manager.py

  VNC credentials (default):
    Username : kali
    Password : kali   (change with option 9 in the menu)
"""

import os
import sys
import time
import json
import signal
import socket
import hashlib
import secrets
import threading
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# ── third-party ──────────────────────────────────────────────────────────────
try:
    import docker
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.live import Live
    from rich.layout import Layout
    from rich.text import Text
    from rich import box
    from rich.columns import Columns
    from rich.rule import Rule
    from rich.syntax import Syntax
    import inquirer
    from tabulate import tabulate
except ImportError:
    print("\n[ERROR] Missing dependencies. Installing...\n")
    pkgs = ["docker", "rich", "blessed", "inquirer", "tabulate", "requests"]
    ret = subprocess.call([
        sys.executable, "-m", "pip", "install", *pkgs,
        "--break-system-packages", "--ignore-installed", "--quiet"
    ])
    if ret != 0:
        ret = subprocess.call([
            sys.executable, "-m", "pip", "install", *pkgs,
            "--user", "--ignore-installed", "--quiet"
        ])
    if ret != 0:
        print("[ERROR] pip install failed. Run manually:")
        print(f"  pip install {' '.join(pkgs)} --break-system-packages --ignore-installed")
        sys.exit(1)
    print("[OK] Dependencies installed. Restarting...\n")
    os.execv(sys.executable, [sys.executable] + sys.argv)

# ── configuration ─────────────────────────────────────────────────────────────
CONFIG_FILE = Path.home() / ".kali_sessions.json"
DATA_DIR    = Path.home() / "kali_sessions"

# ── Docker image ──────────────────────────────────────────────────────────────
# lscr.io/linuxserver/kali-linux exposes:
#   3000/tcp → HTTPS desktop  (self-signed cert, accept in browser)
#   3001/tcp → HTTPS alternate / redirect
#
# FIX v2.2: port 3000 is HTTPS — NOT plain HTTP.
# Connecting with http:// to port 3000 causes:
#   "400 Bad Request — The plain HTTP request was sent to HTTPS port"
# Solution: always use https:// for both ports.
DOCKER_IMAGE        = "lscr.io/linuxserver/kali-linux:latest"
CONTAINER_PORT      = "3000/tcp"   # HTTPS desktop  ← primary
CONTAINER_PORT_ALT  = "3001/tcp"   # HTTPS alternate

PORT_START          = 6081         # host port for first user (increments per user)
PORT_ALT_OFFSET     = 1000         # alternate port = primary port + offset (e.g. 7081)
CPU_LIMIT           = 5          # CPU cores per container
MEM_LIMIT           = "8g"         # RAM per container
MEM_SWAP            = "10g"         # RAM+swap per container
VNC_PASSWORD        = "kali"       # default VNC / login password
PUID                = 1000
PGID                = 1000

console = Console()

# ── helpers ───────────────────────────────────────────────────────────────────

def banner():
    console.print(Panel.fit(
        "[bold red]██╗  ██╗ █████╗ ██╗     ██╗[/bold red]\n"
        "[bold red]██║ ██╔╝██╔══██╗██║     ██║[/bold red]\n"
        "[bold red]█████╔╝ ███████║██║     ██║[/bold red]\n"
        "[bold red]██╔═██╗ ██╔══██║██║     ██║[/bold red]\n"
        "[bold red]██║  ██╗██║  ██║███████╗██║[/bold red]\n"
        "[bold red]╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝[/bold red]\n\n"
        "[cyan bold]  Docker Session Manager  v2.2[/cyan bold]\n"
        "[dim]  Multi-user Kali desktop via browser (HTTPS)[/dim]\n"
        "[dim]  Login with the username & password you set per session[/dim]",
        border_style="red",
        title="[bold white]  KALI  ",
        subtitle="[dim]ctrl+c to exit[/dim]"
    ))


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"sessions": {}, "next_port": PORT_START}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_docker_client():
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception as e:
        console.print(f"\n[bold red][ERROR][/bold red] Cannot connect to Docker: {e}")
        console.print("[yellow]  → Is Docker running?  Try: sudo systemctl start docker[/yellow]\n")
        sys.exit(1)


def container_name(username: str) -> str:
    return f"kali_session_{username}"


def user_data_dir(username: str) -> Path:
    p = DATA_DIR / username
    p.mkdir(parents=True, exist_ok=True)
    return p

# ── session operations ────────────────────────────────────────────────────────

def pull_image_if_needed(client):
    try:
        client.images.get(DOCKER_IMAGE)
        console.print(f"[green]✓[/green] Image [cyan]{DOCKER_IMAGE}[/cyan] already present.")
    except docker.errors.ImageNotFound:
        console.print(f"[yellow]↓[/yellow] Pulling [cyan]{DOCKER_IMAGE}[/cyan] (first run, may take a few minutes)…")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Downloading Kali image…", total=None)
            for line in client.api.pull(DOCKER_IMAGE, stream=True, decode=True):
                status = line.get("status", "")
                progress.update(task, description=f"[cyan]{status[:60]}[/cyan]")
        console.print("[green]✓[/green] Image pulled successfully.")


def create_session(client, cfg: dict, username: str, password: str = VNC_PASSWORD) -> dict:
    cname = container_name(username)

    # Check if already exists
    try:
        existing = client.containers.get(cname)
        console.print(f"[yellow]⚠[/yellow]  Session '[cyan]{username}[/cyan]' already exists (status: {existing.status})")
        return cfg["sessions"].get(username, {})
    except docker.errors.NotFound:
        pass

    port     = cfg["next_port"]
    port_alt = port + PORT_ALT_OFFSET
    cfg["next_port"] += 1
    data_dir = user_data_dir(username)

    pull_image_if_needed(client)

    console.print(f"\n[bold]Creating session for [cyan]{username}[/cyan]…[/bold]")

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        task = p.add_task("Launching container…", total=None)

        container = client.containers.run(
            DOCKER_IMAGE,
            detach=True,
            name=cname,
            hostname=f"kali-{username}",
            environment={
                # credentials — browser HTTP Basic Auth
                # CUSTOM_USER sets the login username (defaults to "abc" if omitted)
                # PASSWORD sets the login password
                "CUSTOM_USER":    username,
                "PASSWORD":       password,
                "VNCPASSWORD":    password,
                "VNC_PW":         password,
                # display
                "RESOLUTION":     "1280x768",
                "PUID":           str(PUID),
                "PGID":           str(PGID),
                "TZ":             "Etc/UTC",
                "DISABLE_LOCK":   "true",
                "NO_SCREEN_LOCK": "true",
            },
            ports={
                # FIX v2.2: port 3000 is HTTPS — map to host as primary port
                CONTAINER_PORT:     ("0.0.0.0", port),
                CONTAINER_PORT_ALT: ("0.0.0.0", port_alt),
            },
            volumes={
                str(data_dir): {"bind": "/home/kali", "mode": "rw"},
            },
            shm_size="512m",
            nano_cpus=int(CPU_LIMIT * 1e9),
            mem_limit=MEM_LIMIT,
            memswap_limit=MEM_SWAP,
            restart_policy={"Name": "unless-stopped"},
            cap_add=["SYS_PTRACE"],
            security_opt=["seccomp=unconfined"],
        )

        p.update(task, description="[green]Container started — waiting for desktop…[/green]")
        time.sleep(5)

    host_ip = get_local_ip()
    _disable_screen_lock(client, cname)

    session = {
        "username":   username,
        "password":   password,
        "port":       port,
        "port_alt":   port_alt,
        "container":  cname,
        "data_dir":   str(data_dir),
        "created_at": datetime.now().isoformat(),
        "status":     "running",
    }
    cfg["sessions"][username] = session
    save_config(cfg)

    # FIX v2.2: both URLs use https:// — port 3000 is HTTPS only
    console.print(Panel(
        f"[bold green]✓  Session ready![/bold green]\n\n"
        f"  [bold]User:[/bold]        [cyan]{username}[/cyan]\n"
        f"  [bold]Password:[/bold]    [yellow]{password}[/yellow]\n\n"
        f"  [bold cyan]── How to connect ──────────────────────────────[/bold cyan]\n"
        f"  [bold]Login:[/bold]       user [yellow]{username}[/yellow]  |  pass [yellow]{password}[/yellow]\n\n"
        f"  [bold]Primary URL:[/bold]\n"
        f"    [link=https://{host_ip}:{port}]https://{host_ip}:{port}[/link]\n"
        f"    [dim]↑ Open this in any browser. Click 'Advanced' → 'Proceed' on the cert warning.[/dim]\n\n"
        f"  [bold]Alternate URL:[/bold]\n"
        f"    [link=https://{host_ip}:{port_alt}]https://{host_ip}:{port_alt}[/link]\n\n"
        f"  [bold]RAM:[/bold]         {MEM_LIMIT}  •  [bold]CPUs:[/bold] {CPU_LIMIT}\n"
        f"  [bold]Data:[/bold]        {data_dir}",
        title="[green]Session Created[/green]",
        border_style="green"
    ))
    return session


def _disable_screen_lock(client, cname: str):
    """Kill screensaver / locker processes and disable DPMS inside the
    container so the VNC desktop is never locked when first opened."""
    cmds = [
        "bash -c 'pkill -f xfce4-screensaver 2>/dev/null; true'",
        "bash -c 'pkill -f xscreensaver       2>/dev/null; true'",
        "bash -c 'pkill -f light-locker        2>/dev/null; true'",
        "bash -c 'DISPLAY=:1 xset s off -dpms 2>/dev/null; true'",
        "bash -c 'DISPLAY=:1 gsettings set org.xfce.screensaver lock-enabled false 2>/dev/null; true'",
        "bash -c 'DISPLAY=:1 gsettings set org.xfce.screensaver enabled       false 2>/dev/null; true'",
    ]
    try:
        c = client.containers.get(cname)
        for cmd in cmds:
            c.exec_run(cmd, user="root", detach=True)
    except Exception:
        pass  # non-fatal


def stop_session(client, cfg: dict, username: str, remove: bool = False):
    cname = container_name(username)
    try:
        c = client.containers.get(cname)
        action = "Removing" if remove else "Stopping"
        console.print(f"[yellow]{action} session '[cyan]{username}[/cyan]'…[/yellow]")
        if remove:
            c.remove(force=True)
            cfg["sessions"].pop(username, None)
            console.print(f"[green]✓[/green]  Session '[cyan]{username}[/cyan]' removed.")
        else:
            c.stop(timeout=10)
            if username in cfg["sessions"]:
                cfg["sessions"][username]["status"] = "stopped"
            console.print(f"[green]✓[/green]  Session '[cyan]{username}[/cyan]' stopped.")
        save_config(cfg)
    except docker.errors.NotFound:
        console.print(f"[red]✗[/red]  Container not found: [cyan]{cname}[/cyan]")


def start_session(client, cfg: dict, username: str):
    cname = container_name(username)
    try:
        c = client.containers.get(cname)
        c.start()
        if username in cfg["sessions"]:
            cfg["sessions"][username]["status"] = "running"
        save_config(cfg)
        host_ip  = get_local_ip()
        port     = cfg["sessions"][username]["port"]
        port_alt = cfg["sessions"][username].get("port_alt", port + PORT_ALT_OFFSET)
        time.sleep(4)
        _disable_screen_lock(client, cname)
        console.print(f"[green]✓[/green]  Session '[cyan]{username}[/cyan]' started.")
        # FIX v2.2: https:// for both
        console.print(f"  Primary  → [link=https://{host_ip}:{port}]https://{host_ip}:{port}[/link]")
        console.print(f"  Alternate→ [link=https://{host_ip}:{port_alt}]https://{host_ip}:{port_alt}[/link]")
        console.print(f"  [dim]Login: user [yellow]{username}[/yellow] | pass [yellow]{cfg['sessions'][username]['password']}[/yellow][/dim]")
    except docker.errors.NotFound:
        console.print(f"[red]✗[/red]  No container for user '[cyan]{username}[/cyan]'. Create a session first.")


def get_container_status(client, username: str) -> str:
    try:
        c = client.containers.get(container_name(username))
        return c.status
    except docker.errors.NotFound:
        return "missing"


def list_sessions(client, cfg: dict):
    sessions = cfg.get("sessions", {})
    if not sessions:
        console.print("\n[yellow]No sessions found.[/yellow] Use [bold]Create Session[/bold] to add users.\n")
        return

    host_ip = get_local_ip()
    table = Table(
        title=f"\n[bold]Active Sessions — Host: {host_ip}[/bold]",
        box=box.ROUNDED,
        border_style="cyan",
        show_lines=True
    )
    table.add_column("User",     style="cyan bold",  no_wrap=True)
    table.add_column("Status",   justify="center")
    table.add_column("Port",     justify="center",   style="yellow")
    table.add_column("Alt Port", justify="center",   style="dim")
    # FIX v2.2: label is HTTPS, not HTTP
    table.add_column("URL (HTTPS)", style="blue")
    table.add_column("Password", style="yellow")
    table.add_column("RAM",      justify="center")
    table.add_column("CPUs",     justify="center")
    table.add_column("Created",  style="dim")

    for uname, s in sessions.items():
        live_status = get_container_status(client, uname)
        status_str = {
            "running":    "[green]● running[/green]",
            "stopped":    "[red]○ stopped[/red]",
            "restarting": "[yellow]↻ restarting[/yellow]",
            "paused":     "[yellow]⏸ paused[/yellow]",
            "missing":    "[dim]✗ missing[/dim]",
        }.get(live_status, f"[dim]{live_status}[/dim]")

        created  = s.get("created_at", "—")[:16].replace("T", " ")
        port     = s.get("port", "—")
        port_alt = s.get("port_alt", "—")
        password = s.get("password", "—")

        table.add_row(
            uname,
            status_str,
            str(port),
            str(port_alt),
            f"https://{host_ip}:{port}",   # FIX v2.2: https://
            password,
            MEM_LIMIT,
            str(CPU_LIMIT),
            created,
        )

    console.print(table)
    console.print("[dim]  Open the HTTPS URL in any browser. Click 'Advanced' → 'Proceed' to accept the self-signed cert.[/dim]")
    console.print("[dim]  Login: username = your session username | password = shown above[/dim]\n")


def exec_in_session(client, username: str, command: str):
    cname = container_name(username)
    try:
        c = client.containers.get(cname)
        console.print(f"\n[bold]Running in [cyan]{username}[/cyan]:[/bold] {command}\n")
        exit_code, output = c.exec_run(command, stream=True, tty=False)
        for chunk in output:
            console.print(chunk.decode(errors="replace"), end="")
        console.print()
    except docker.errors.NotFound:
        console.print(f"[red]✗[/red]  Container not found: [cyan]{cname}[/cyan]")


def session_logs(client, username: str, tail: int = 50):
    cname = container_name(username)
    try:
        c = client.containers.get(cname)
        logs = c.logs(tail=tail).decode(errors="replace")
        syntax = Syntax(logs, "bash", theme="monokai", line_numbers=False)
        console.print(Panel(syntax, title=f"[bold]Logs — {username}[/bold]", border_style="cyan"))
    except docker.errors.NotFound:
        console.print(f"[red]✗[/red]  Container not found: [cyan]{cname}[/cyan]")


def container_stats(client, cfg: dict):
    sessions = cfg.get("sessions", {})
    if not sessions:
        console.print("[yellow]No sessions to monitor.[/yellow]")
        return

    console.print("\n[bold cyan]Live resource usage[/bold cyan] (press Ctrl+C to stop)\n")
    try:
        while True:
            rows = []
            for uname in sessions:
                try:
                    c = client.containers.get(container_name(uname))
                    if c.status != "running":
                        rows.append([uname, c.status, "—", "—", "—"])
                        continue
                    stats = c.stats(stream=False)
                    cpu_d   = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                              stats["precpu_stats"]["cpu_usage"]["total_usage"]
                    sys_d   = stats["cpu_stats"].get("system_cpu_usage", 0) - \
                              stats["precpu_stats"].get("system_cpu_usage", 1)
                    ncpu    = stats["cpu_stats"].get("online_cpus", 1)
                    cpu_pct = (cpu_d / sys_d) * ncpu * 100 if sys_d else 0

                    mem_u   = stats["memory_stats"].get("usage", 0)
                    mem_l   = stats["memory_stats"].get("limit", 1)
                    mem_pct = mem_u / mem_l * 100

                    net_rx = stats.get("networks", {})
                    rx = sum(v.get("rx_bytes", 0) for v in net_rx.values())
                    tx = sum(v.get("tx_bytes", 0) for v in net_rx.values())

                    rows.append([
                        uname,
                        "[green]running[/green]",
                        f"{cpu_pct:.1f}%",
                        f"{mem_u//1024//1024}MB / {mem_l//1024//1024}MB ({mem_pct:.0f}%)",
                        f"↓{rx//1024}K  ↑{tx//1024}K",
                    ])
                except Exception:
                    rows.append([uname, "error", "—", "—", "—"])

            os.system("clear")
            console.print(Panel(
                tabulate(rows,
                         headers=["User", "Status", "CPU", "Memory", "Network"],
                         tablefmt="rounded_outline"),
                title="[bold cyan]Session Stats[/bold cyan]",
                subtitle="[dim]Ctrl+C to exit[/dim]"
            ))
            time.sleep(3)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped monitoring.[/dim]")


def shell_into_session(client, username: str):
    cname = container_name(username)
    try:
        client.containers.get(cname)
        console.print(f"\n[bold]Opening shell in [cyan]{username}[/cyan]…[/bold] (type [red]exit[/red] to return)\n")
        os.system(f"docker exec -it {cname} /bin/bash")
    except docker.errors.NotFound:
        console.print(f"[red]✗[/red]  Container not found.")


def backup_session(cfg: dict, username: str):
    s = cfg["sessions"].get(username)
    if not s:
        console.print(f"[red]No session found for {username}[/red]")
        return
    data_dir    = s["data_dir"]
    backup_name = f"kali_backup_{username}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
    backup_path = Path.home() / backup_name
    console.print(f"[yellow]Backing up [cyan]{username}[/cyan] data…[/yellow]")
    ret = os.system(f"tar -czf {backup_path} -C {data_dir} .")
    if ret == 0:
        console.print(f"[green]✓[/green]  Backup saved: [bold]{backup_path}[/bold]")
    else:
        console.print("[red]✗[/red]  Backup failed.")


def generate_report(client, cfg: dict):
    host_ip  = get_local_ip()
    sessions = cfg.get("sessions", {})
    lines = [
        "KALI DOCKER SESSION REPORT",
        f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Host IP   : {host_ip}",
        f"Sessions  : {len(sessions)}",
        "=" * 60,
    ]
    for uname, s in sessions.items():
        status   = get_container_status(client, uname)
        port     = s['port']
        port_alt = s.get('port_alt', port + PORT_ALT_OFFSET)
        lines += [
            f"\nUser         : {uname}",
            f"Status       : {status}",
            f"Login        : user={uname}  pass={s.get('password','kali')}",
            # FIX v2.2: https:// URLs
            f"URL (primary): https://{host_ip}:{port}",
            f"URL (alt)    : https://{host_ip}:{port_alt}",
            f"Port         : {port}",
            f"Storage      : {MEM_LIMIT} RAM / {CPU_LIMIT} CPUs",
            f"Created      : {s.get('created_at','—')[:16]}",
            f"Data dir     : {s['data_dir']}",
        ]

    report_path = Path.home() / f"kali_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    report_path.write_text("\n".join(lines))
    console.print(f"[green]✓[/green]  Report saved: [bold]{report_path}[/bold]")
    console.print(Syntax("\n".join(lines), "text", theme="monokai"))


def change_password(client, cfg: dict, username: str, new_password: str):
    cname = container_name(username)
    try:
        c = client.containers.get(cname)
        # Update the HTTP Basic Auth password via env isn't live — restart needed.
        # Also update the system user password inside the container.
        c.exec_run(f"bash -c \"echo '{username}:{new_password}' | chpasswd 2>/dev/null; true\"")
        if username in cfg["sessions"]:
            cfg["sessions"][username]["password"] = new_password
        save_config(cfg)
        console.print(f"[green]✓[/green]  Password updated for [cyan]{username}[/cyan].")
        console.print(f"[yellow]⚠[/yellow]  Restart the container for the new browser login password to take effect:")
        console.print(f"  docker restart {cname}")
    except docker.errors.NotFound:
        console.print("[red]✗[/red]  Container not found.")


def bulk_create(client, cfg: dict, count: int, prefix: str, password: str):
    console.print(f"\n[bold]Creating {count} sessions with prefix '[cyan]{prefix}[/cyan]'…[/bold]\n")
    for i in range(1, count + 1):
        uname = f"{prefix}{i:02d}"
        create_session(client, cfg, uname, password)
        time.sleep(1)
    console.print(f"\n[green]✓[/green]  {count} sessions created.")

# ── main menu ─────────────────────────────────────────────────────────────────

MENU_ITEMS = [
    ("1", "📋  List all sessions",          "list"),
    ("2", "➕  Create session",              "create"),
    ("3", "🗑️   Remove session",             "remove"),
    ("4", "⏹️   Stop session",               "stop"),
    ("5", "▶️   Start session",              "start"),
    ("6", "💻  Shell into session",          "shell"),
    ("7", "📜  View session logs",           "logs"),
    ("8", "📊  Live stats monitor",          "stats"),
    ("9", "🔑  Change user password",        "passwd"),
    ("A", "💾  Backup session data",         "backup"),
    ("B", "⚡  Bulk create sessions",        "bulk"),
    ("C", "📝  Run command in session",      "exec"),
    ("D", "📄  Generate report",             "report"),
    ("E", "🐳  Pull/update Docker image",    "pull"),
    ("Q", "🚪  Quit",                        "quit"),
]


def print_menu():
    console.print()
    console.print(Rule("[bold cyan]Main Menu[/bold cyan]"))

    left  = MENU_ITEMS[:8]
    right = MENU_ITEMS[8:]

    def fmt(key, label, _):
        return f"  [bold yellow]{key}[/bold yellow]  {label}"

    left_lines  = [fmt(*m) for m in left]
    right_lines = [fmt(*m) for m in right]

    for i in range(max(len(left_lines), len(right_lines))):
        l = left_lines[i]  if i < len(left_lines)  else ""
        r = right_lines[i] if i < len(right_lines) else ""
        console.print(f"{l:<45}{r}")
    console.print()


def pick_user(cfg: dict, prompt_text: str = "Select user") -> str | None:
    sessions = cfg.get("sessions", {})
    if not sessions:
        console.print("[yellow]No sessions available.[/yellow]")
        return None
    choices = list(sessions.keys())
    q = [inquirer.List("user", message=prompt_text, choices=choices)]
    ans = inquirer.prompt(q)
    return ans["user"] if ans else None


def main():
    banner()
    client = get_docker_client()
    cfg    = load_config()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    console.print(f"[dim]Docker connected  •  Config: {CONFIG_FILE}  •  Data: {DATA_DIR}[/dim]")
    console.print(f"[dim]Sessions use [bold]HTTPS[/bold] — open URL in browser and accept the self-signed certificate[/dim]")
    console.print(f"[dim]Login: username = the session username  |  password = set at creation (default: [yellow]kali[/yellow])[/dim]\n")

    while True:
        print_menu()
        choice = Prompt.ask(
            "[bold cyan]Choose option[/bold cyan]",
            default="1"
        ).strip().upper()

        # ── list ───────────────────────────────────────────────────────────
        if choice == "1":
            list_sessions(client, cfg)

        # ── create ─────────────────────────────────────────────────────────
        elif choice == "2":
            username = Prompt.ask("[cyan]Username[/cyan]").strip()
            if not username:
                console.print("[red]Username cannot be empty.[/red]")
                continue
            password = Prompt.ask(
                "[cyan]VNC/Login password[/cyan]",
                default=VNC_PASSWORD,
                password=False
            )
            create_session(client, cfg, username, password)

        # ── remove ─────────────────────────────────────────────────────────
        elif choice == "3":
            username = pick_user(cfg, "Remove which user?")
            if username and Confirm.ask(f"[red]Permanently remove session for '{username}'?[/red]"):
                stop_session(client, cfg, username, remove=True)

        # ── stop ───────────────────────────────────────────────────────────
        elif choice == "4":
            username = pick_user(cfg, "Stop which session?")
            if username:
                stop_session(client, cfg, username)

        # ── start ──────────────────────────────────────────────────────────
        elif choice == "5":
            username = pick_user(cfg, "Start which session?")
            if username:
                start_session(client, cfg, username)

        # ── shell ──────────────────────────────────────────────────────────
        elif choice == "6":
            username = pick_user(cfg, "Shell into which session?")
            if username:
                shell_into_session(client, username)

        # ── logs ───────────────────────────────────────────────────────────
        elif choice == "7":
            username = pick_user(cfg, "Logs for which session?")
            if username:
                tail = int(Prompt.ask("Lines of log", default="80"))
                session_logs(client, username, tail)

        # ── stats ──────────────────────────────────────────────────────────
        elif choice == "8":
            container_stats(client, cfg)

        # ── password ───────────────────────────────────────────────────────
        elif choice == "9":
            username = pick_user(cfg, "Change password for?")
            if username:
                new_pw = Prompt.ask("[cyan]New password[/cyan]", password=True)
                change_password(client, cfg, username, new_pw)

        # ── backup ─────────────────────────────────────────────────────────
        elif choice == "A":
            username = pick_user(cfg, "Backup which session?")
            if username:
                backup_session(cfg, username)

        # ── bulk ───────────────────────────────────────────────────────────
        elif choice == "B":
            prefix   = Prompt.ask("[cyan]Username prefix[/cyan]", default="student")
            count    = int(Prompt.ask("[cyan]Number of sessions[/cyan]", default="5"))
            password = Prompt.ask("[cyan]Password for all[/cyan]", default=VNC_PASSWORD)
            bulk_create(client, cfg, count, prefix, password)

        # ── exec ───────────────────────────────────────────────────────────
        elif choice == "C":
            username = pick_user(cfg, "Run command in which session?")
            if username:
                cmd = Prompt.ask("[cyan]Command to run[/cyan]")
                exec_in_session(client, username, cmd)

        # ── report ─────────────────────────────────────────────────────────
        elif choice == "D":
            generate_report(client, cfg)

        # ── pull image ─────────────────────────────────────────────────────
        elif choice == "E":
            console.print(f"[yellow]Pulling latest [cyan]{DOCKER_IMAGE}[/cyan]…[/yellow]")
            for line in client.api.pull(DOCKER_IMAGE, stream=True, decode=True):
                status = line.get("status", "")
                if status:
                    console.print(f"  [dim]{status[:80]}[/dim]")
            console.print("[green]✓[/green]  Image updated.")

        # ── quit ───────────────────────────────────────────────────────────
        elif choice in ("Q", "QUIT", "EXIT"):
            console.print("\n[bold red]Goodbye![/bold red] Sessions keep running in Docker.\n")
            break

        else:
            console.print("[yellow]Unknown option. Try again.[/yellow]")

        console.print()
        input("  Press Enter to continue…")
        os.system("clear")
        banner()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: (console.print("\n[red]Interrupted.[/red]"), sys.exit(0)))
    main()
