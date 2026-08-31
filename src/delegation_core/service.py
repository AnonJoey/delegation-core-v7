"""
service.py — install the HTTP daemon as a per-user background service.

Under stdio nobody had to think about lifecycle: the client spawned the server
and reaped it. An HTTP daemon has to already be running when the first client
connects, so something has to start it. That something is the OS's own per-user
service manager, one per platform:

    Linux    systemd user unit    ~/.config/systemd/user/delegation-core.service
    macOS    launchd agent        ~/Library/LaunchAgents/com.delegation-core.plist
    Windows  Task Scheduler       logon task "delegation-core"

That mapping is not invented here — wizard.py already names these three as the
project's convention, and downloader.py/engine.py already branch the same way.

Per-user, never system-wide, in all three cases. The daemon reads a vault under
$HOME, talks to a loopback port, and holds a token that lives in the user's
config; running it as root or as a machine service would be wrong on every one
of those counts.

Everything here is text generation plus one subprocess call to the platform's
own tool. Nothing parses the service manager's state format beyond what it
prints, because those formats are the least stable part of any of these systems.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("service")

SERVICE_NAME = "delegation-core"
LAUNCHD_LABEL = "com.delegation-core"

SYSTEMD_UNIT = Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
WIN_STARTUP_DIR = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
WIN_STARTUP_CMD = WIN_STARTUP_DIR / f"{SERVICE_NAME}.cmd"


def _executable() -> str:
    """Absolute path to the installed console script.

    Absolute on purpose. The console script lives in the install's venv and is
    routinely absent from PATH — on this machine `which delegation-core` finds
    nothing while the binary sits in ~/.delegation_core/venv/bin. A service
    manager starts with an even thinner environment than a shell does, so a bare
    name here is a service that silently never starts.
    """
    found = shutil.which(SERVICE_NAME)
    if found:
        return found
    candidate = Path(sys.executable).parent / SERVICE_NAME
    if candidate.exists():
        return str(candidate)
    return SERVICE_NAME


def systemd_unit_text() -> str:
    return f"""[Unit]
Description=delegation-core MCP daemon
Documentation=https://github.com/Grimstone-Solutions/delegation-core
After=network.target
# The daemon loads BGE onto the GPU at startup, so a crash loop would thrash it.
# These are [Unit] keys, not [Service] ones — systemd-analyze verify rejects them
# under [Service] with "Unknown key ... ignoring", which fails silently at runtime.
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
ExecStart={_executable()} run
Restart=on-failure
RestartSec=5
# The daemon writes to a ChromaDB index that cannot be interrupted safely, and
# its normal work — a full reindex, a relink pass — runs for minutes. systemd's
# default stop timeout is far shorter than that, and when it expires the daemon
# is SIGKILLed mid-write.
#
# Observed, not theorised: a restart issued during a relink hit the 10s ceiling
# on this machine, SIGKILLed the process with ChromaDB mid-operation, and the
# next two starts died with SIGSEGV inside chromadb_rust_bindings on a
# tokio-rt-worker thread. Only the third came up. The index survived, but the
# same interruption is what leaves HNSW segments without their SQLite rows —
# the ghost-row failure this project has already had to defend search against.
#
# Ten minutes is longer than any observed shutdown and still bounded, so a
# genuinely wedged process is still reaped rather than hanging the stop forever.
TimeoutStopSec=600

[Install]
WantedBy=default.target
"""


def launchd_plist_text() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{_executable()}</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <!-- Processes under launchd inherit maxfiles 256. This daemon walks tens of
       thousands of files during ingest and holds a ChromaDB/SQLite index open
       while doing it, so the default is a ceiling it has no reason to sit
       under. Raising it is free; hitting it produces failures that look like
       corruption rather than exhaustion. -->
  <key>SoftResourceLimits</key>
  <dict><key>NumberOfFiles</key><integer>16384</integer></dict>
  <!-- launchd's default is 20 seconds between SIGTERM and SIGKILL. This daemon
       writes to a ChromaDB index during work that runs for minutes, and being
       killed mid-write is what leaves HNSW segments without their SQLite rows.
       The systemd unit carries the same reasoning at TimeoutStopSec. -->
  <key>ExitTimeOut</key><integer>600</integer>
</dict>
</plist>
"""


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} timed out"


def install() -> dict:
    system = platform.system()

    if system == "Linux":
        SYSTEMD_UNIT.parent.mkdir(parents=True, exist_ok=True)
        SYSTEMD_UNIT.write_text(systemd_unit_text(), encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])
        code, out = _run(["systemctl", "--user", "enable", "--now", SERVICE_NAME])
        return {"platform": system, "unit": str(SYSTEMD_UNIT),
                "status": "installed" if code == 0 else "written_but_not_started",
                "detail": out,
                "hint": ("Run `loginctl enable-linger $USER` if the daemon should "
                         "survive logout.")}

    if system == "Darwin":
        LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHD_PLIST.write_text(launchd_plist_text(), encoding="utf-8")
        code, out = _run(["launchctl", "load", "-w", str(LAUNCHD_PLIST)])
        return {"platform": system, "unit": str(LAUNCHD_PLIST),
                "status": "installed" if code == 0 else "written_but_not_started",
                "detail": out}

    if system == "Windows":
        code, out = _run([
            "schtasks", "/Create", "/TN", SERVICE_NAME, "/SC", "ONLOGON",
            "/TR", f'"{_executable()}" run', "/F",
        ])
        if code == 0:
            return {"platform": system, "unit": f"Task Scheduler: {SERVICE_NAME}",
                    "status": "installed", "detail": out,
                    "hint": "The task runs at logon; start it now with `schtasks /Run /TN delegation-core`."}
        try:
            WIN_STARTUP_DIR.mkdir(parents=True, exist_ok=True)
            WIN_STARTUP_CMD.write_text(f'@echo off\r\nstart "" /B "{_executable()}" run\r\n', encoding="utf-8")
            return {"platform": system, "unit": str(WIN_STARTUP_CMD),
                    "status": "installed", "detail": "Configured via user Startup folder (no elevation required)",
                    "hint": "The script runs at logon from your Startup folder."}
        except Exception as e:
            return {"platform": system, "unit": f"Task Scheduler: {SERVICE_NAME}",
                    "status": "failed", "detail": f"{out}; Startup folder fallback failed: {e}"}

    return {"platform": system, "status": "unsupported",
            "detail": f"No service integration for {system}. Run `{_executable()} run` yourself."}


def uninstall() -> dict:
    system = platform.system()

    if system == "Linux":
        _run(["systemctl", "--user", "disable", "--now", SERVICE_NAME])
        existed = SYSTEMD_UNIT.exists()
        SYSTEMD_UNIT.unlink(missing_ok=True)
        _run(["systemctl", "--user", "daemon-reload"])
        return {"platform": system, "status": "removed" if existed else "not_installed"}

    if system == "Darwin":
        _run(["launchctl", "unload", "-w", str(LAUNCHD_PLIST)])
        existed = LAUNCHD_PLIST.exists()
        LAUNCHD_PLIST.unlink(missing_ok=True)
        return {"platform": system, "status": "removed" if existed else "not_installed"}

    if system == "Windows":
        code, out = _run(["schtasks", "/Delete", "/TN", SERVICE_NAME, "/F"])
        cmd_existed = False
        if WIN_STARTUP_CMD.exists():
            WIN_STARTUP_CMD.unlink(missing_ok=True)
            cmd_existed = True
        return {"platform": system, "status": "removed" if (code == 0 or cmd_existed) else "not_installed",
                "detail": out}

    return {"platform": system, "status": "unsupported"}


def status() -> dict:
    """Report what the platform's service manager says, plus whether the port answers.

    Both, because they answer different questions and can disagree in a way that
    matters: "the unit is active" does not mean the daemon finished loading BGE,
    and "the port answers" does not mean anything will restart it after a reboot.
    """
    system = platform.system()
    result: dict = {"platform": system, "reachable": _port_answers()}

    if system == "Linux":
        code, out = _run(["systemctl", "--user", "is-active", SERVICE_NAME])
        result.update(installed=SYSTEMD_UNIT.exists(), manager_state=out or "unknown")
    elif system == "Darwin":
        code, out = _run(["launchctl", "list", LAUNCHD_LABEL])
        result.update(installed=LAUNCHD_PLIST.exists(),
                      manager_state="loaded" if code == 0 else "not loaded")
    elif system == "Windows":
        code, out = _run(["schtasks", "/Query", "/TN", SERVICE_NAME])
        startup_exists = WIN_STARTUP_CMD.exists()
        result.update(installed=(code == 0 or startup_exists),
                      manager_state=out.splitlines()[-1] if out else ("startup folder" if startup_exists else "unknown"))
    else:
        result.update(installed=False, manager_state="unsupported")
    return result


def _port_answers() -> bool:
    """Cheap liveness probe: is something listening on the configured port?

    A TCP connect rather than an MCP handshake: this is a lifecycle question,
    and an unauthenticated probe cannot complete a handshake anyway now that the
    transport requires a token.
    """
    import socket
    from .config import Config
    cfg = Config.load()
    try:
        with socket.create_connection((cfg.server_host, cfg.server_port), timeout=1.0):
            return True
    except OSError:
        return False
