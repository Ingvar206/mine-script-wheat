#!/usr/bin/env python3
"""
wheat_watchdog.py – Erkennt Kicks und führt nach Reconnect (via Mod) /skyblock /warp garden aus.
Starten: python3 wheat_watchdog.py
"""
import os
import sys
import time
import subprocess
from pathlib import Path

PROFILE   = Path.home() / "Library/Application Support/ModrinthApp/profiles/New instance (1)"
LOG_FILE  = PROFILE / "logs/latest.log"
RUN_FILE  = PROFILE / "minescript/wheat.running"
PID_FILE  = PROFILE / "minescript/wheat_watchdog.pid"

KICK_PATTERNS = (
    "disconnecting:",
    "connection lost",
    "connection timed out",
    "you have been kicked",
    "lost connection",
    "internal exception",
)

JOIN_PATTERNS = (
    "joining world",
    "connecting to mc.hypixel.net",
    "connecting to hypixel",
)

POST_JOIN_WAIT = 20   # Sek. nach Join warten bis Commands gesendet werden


def _osascript(script: str) -> None:
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def _bring_minecraft_to_front() -> None:
    _osascript("""
        tell application "System Events"
            set mc_procs to every process whose name is "java"
            if length of mc_procs > 0 then
                set frontmost of item 1 of mc_procs to true
            end if
        end tell
    """)


def _send_chat(cmd: str) -> None:
    _bring_minecraft_to_front()
    time.sleep(0.4)
    _osascript('tell application "System Events" to key code 17')  # T = Chat
    time.sleep(0.6)
    escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
    _osascript(f'tell application "System Events" to keystroke "{escaped}"')
    time.sleep(0.3)
    _osascript('tell application "System Events" to key code 36')  # Enter
    time.sleep(0.5)


def _do_rejoin(log_pos: int) -> int:
    print(f"[Watchdog] Warte {POST_JOIN_WAIT}s auf Ladevorgang...")
    time.sleep(POST_JOIN_WAIT)

    if LOG_FILE.exists():
        log_pos = LOG_FILE.stat().st_size

    print("[Watchdog] Sende Sequenz...")
    _send_chat("/skyblock")
    time.sleep(4)
    _send_chat("/warp garden")
    time.sleep(6)

    if not RUN_FILE.exists():
        print("[Watchdog] Starte Wheat-Script...")
        _send_chat("\\wheat start")
    else:
        print("[Watchdog] Wheat-Script läuft bereits.")

    return log_pos


def main() -> None:
    log_pos = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
    kicked = False

    print("[Watchdog] Gestartet – Reconnect-Mod übernimmt den Reconnect")
    print(f"[Watchdog] Überwache: {LOG_FILE}")
    print("[Watchdog] Strg+C zum Beenden\n")

    while True:
        time.sleep(2)

        if not LOG_FILE.exists():
            continue

        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                f.seek(log_pos)
                chunk = f.read()
                log_pos = f.tell()
        except Exception:
            continue

        for line in chunk.splitlines():
            ll = line.lower()

            if not kicked and any(p in ll for p in KICK_PATTERNS):
                print(f"[Watchdog] Kick erkannt: {line.strip()}")
                kicked = True

            elif kicked and any(p in ll for p in JOIN_PATTERNS):
                print(f"[Watchdog] Reconnect erkannt: {line.strip()}")
                kicked = False
                log_pos = _do_rejoin(log_pos)


if __name__ == "__main__":
    # Prüfen ob bereits eine Instanz läuft
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text())
            os.kill(pid, 0)  # Wirft Exception wenn Prozess nicht existiert
            print(f"[Watchdog] Läuft bereits (PID {pid}) – beende.")
            sys.exit(0)
        except (ProcessLookupError, ValueError):
            pass  # Prozess tot oder PID ungültig → weitermachen

    PID_FILE.write_text(str(os.getpid()))
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Watchdog] Beendet.")
    finally:
        PID_FILE.unlink(missing_ok=True)
