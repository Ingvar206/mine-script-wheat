import sys
import time
import math
import random
import re
import json
import os
import subprocess
from pathlib import Path

from minescript import (
    chat,
    echo,
    player_press_attack,
    player_press_jump,
    player_press_use,
    player,
    player_get_targeted_block,
    player_press_forward,
    player_press_backward,
    player_press_left,
    player_press_right,
    player_look_at,
    player_set_orientation,
    entities,
)

import minescript as _ms
_set_hotbar_slot = None
for _fname in ('player_inventory_select_slot', 'player_set_hotbar_slot',
               'player_press_hotbar_slot', 'select_hotbar_slot',
               'player_hotbar_slot', 'hotbar_slot'):
    _fn = getattr(_ms, _fname, None)
    if _fn is not None:
        _set_hotbar_slot = _fn
        break
_HOTBAR_FUNC_NAME = _fname if _set_hotbar_slot is not None else None
_HOTBAR_CANDIDATES = [a for a in dir(_ms) if 'hotbar' in a.lower() or 'slot' in a.lower()]
if _set_hotbar_slot is None:
    def _set_hotbar_slot(n): pass


DEBUG_ENTITIES = False
DEBUG_LOG = True  # Alle neuen Log-Zeilen anzeigen um Pest-Nachrichten-Format zu finden

RUN_FILE      = Path(__file__).with_suffix(".running")
STATE_FILE    = Path(__file__).with_suffix(".json")
WATCHDOG_FILE = Path(__file__).with_name("wheat_watchdog.py")
PID_FILE      = Path(__file__).with_name("wheat_watchdog.pid")

# Minecraft latest.log – Server-Nachrichten werden hier geschrieben (plotübergreifend)
_PROFILE_DIR = Path(__file__).parent.parent  # .../profiles/New instance (1)
LOG_FILE = _PROFILE_DIR / "logs" / "latest.log"

# Hypixels Pest-Spawn-Nachricht enthält immer das ൠ-Symbol – präziser als Keywords
PEST_SPAWN_SYMBOL = 'ൠ'

# Eigene Echo-Nachrichten die im Log ignoriert werden sollen
_OWN_ECHO_PREFIXES = ("[Pest]", "[LOG]", "[DBG]", "=== wheat", "hotbar:",
                      "Schaedling", "Warp ", "Alle Schae", "Setze Farm",
                      "Pest-Counter", "Stop wheat", "Kein Weizen")


def _load_pest_count() -> int:
    try:
        return int(json.loads(STATE_FILE.read_text(encoding="utf-8")).get("pest_count", 0))
    except Exception:
        return 0


def _save_pest_count(n: int) -> None:
    try:
        STATE_FILE.write_text(json.dumps({"pest_count": n}), encoding="utf-8")
    except Exception as _e:
        echo(f"[State] Speichern fehlgeschlagen: {_e}")


def _parse_pest_increment(chat_line: str) -> int:
    """Extrahiert Pest-Anzahl aus der Nachricht anhand des ൠ-Symbols.
    '§22 §2ൠ Pest' → strip Farbcodes → '2 ൠ Pest' → 2."""
    clean = re.sub(r'§.', '', chat_line)
    idx = clean.find(PEST_SPAWN_SYMBOL)
    if idx > 0:
        m = re.search(r'\b([2-9])\b', clean[:idx])
        return int(m.group(1)) if m else 1
    return 1
STRAFE_STABLE_MIN = 1
STRAFE_STABLE_MAX = 1.1

# Farm bounds
FARM_X_MIN, FARM_X_MAX = -238, -48
FARM_Z_MIN, FARM_Z_MAX = -50, 50
FARM_Y = 70

# Plots: Grenze bei X=-143; Check-Punkte in der Mitte jedes Plots
PLOT_BOUNDARY_X = -143
PLOT1_CHECK_POS = (-190, FARM_Y + 3, 0)   # West-Plot
PLOT2_CHECK_POS = (-95,  FARM_Y + 3, 0)   # Ost-Plot

# Pest handling
PEST_CHECK_INTERVAL = 3.0   # Sekunden zwischen Entity-Scans
NAV_CLOSE_ENOUGH    = 3.0   # Blöcke bis "nah genug"
NAV_TIMEOUT         = 15.0  # Max. Sekunden pro Navigation
PEST_KILL_TIMEOUT   = 30.0  # Max. Sekunden pro Schädlingstötung

# Entity-Typen, die kein Schädling sein können
_SKIP_ENTITY_TYPES = {
    'player', 'armor_stand', 'item', 'experience_orb',
    'item_frame', 'glow_item_frame', 'painting',
    'text_display', 'item_display', 'block_display',
    'falling_block', 'tnt', 'firework_rocket',
}


def _stop_all_keys() -> None:
    player_press_forward(False)
    player_press_backward(False)
    player_press_left(False)
    player_press_right(False)
    player_press_attack(False)
    player_press_jump(False)
    player_press_use(False)


def _toggle_flight() -> None:
    """Doppel-Leertaste um Fliegen ein/auszuschalten."""
    player_press_jump(True)
    time.sleep(0.05)
    player_press_jump(False)
    time.sleep(0.05)
    player_press_jump(True)
    time.sleep(0.05)
    player_press_jump(False)
    time.sleep(0.15)


def _fly_to(tx: float, ty: float, tz: float, stop_dist: float = NAV_CLOSE_ENOUGH) -> None:
    """Fliege zu einer festen Position auf sicherer Höhe."""
    player_press_forward(True)
    deadline = time.time() + NAV_TIMEOUT
    while time.time() < deadline:
        if not RUN_FILE.exists():
            player_press_forward(False)
            _stop_all_keys()
            return
        try:
            p = player()
            px = float(p.position[0])
            pz = float(p.position[2])
            xz_dist = math.sqrt((tx - px) ** 2 + (tz - pz) ** 2)
            if xz_dist < stop_dist:
                break
            player_look_at(tx, ty, tz)
        except Exception:
            pass
        time.sleep(0.1)
    player_press_forward(False)
    player_press_backward(True)
    time.sleep(0.25)
    player_press_backward(False)


def _wait_land(timeout: float = 4.0) -> None:
    """Wartet bis der Spieler gelandet ist (Y stabilisiert sich)."""
    deadline = time.time() + timeout
    last_y = None
    while time.time() < deadline:
        try:
            cy = float(player().position[1])
            if last_y is not None and abs(cy - last_y) < 0.05:
                break
            last_y = cy
        except Exception:
            pass
        time.sleep(0.1)
    time.sleep(0.2)


def _walk_to(tx: float, ty: float, tolerance: float = 0.05) -> None:
    """Läuft zu Fuß zur Zielposition. Nur X muss auf 0.05 genau stimmen, Z wird ignoriert."""
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if not RUN_FILE.exists():
            break
        try:
            p = player()
            px, pz = float(p.position[0]), float(p.position[2])
            if abs(tx - px) < tolerance:
                break
            # Z der aktuellen Position halten – nur X korrigieren
            player_look_at(tx, ty, pz)
            player_press_forward(True)
        except Exception:
            pass
        time.sleep(0.05)
    player_press_forward(False)
    player_press_backward(True)
    time.sleep(0.15)
    player_press_backward(False)


def _eattr(e, name, default=''):
    return getattr(e, name, default)


def _epos(e):
    pos = getattr(e, 'position', None)
    if pos is None:
        return None
    try:
        if hasattr(pos, 'x'):
            return float(pos.x), float(pos.y), float(pos.z)
        return float(pos[0]), float(pos[1]), float(pos[2])
    except (TypeError, IndexError, ValueError):
        return None


def _get_pests() -> list:
    try:
        ents = entities()
    except Exception as err:
        echo(f"entities() Fehler: {err}")
        return []

    if DEBUG_ENTITIES:
        non_skip = [e for e in ents if str(_eattr(e, 'type')).lower().split('.')[-1] not in _SKIP_ENTITY_TYPES]
        echo(f"[DBG] total={len(ents)} mobs={len(non_skip)}")
        for e in non_skip[:8]:
            echo(f"  typ={_eattr(e,'type')} pos={_epos(e)} name={_eattr(e,'name') or _eattr(e,'custom_name')}")

    pests = []
    for e in ents:
        etype = str(_eattr(e, 'type')).lower().split('.')[-1]  # strip "entity.minecraft."
        if etype in _SKIP_ENTITY_TYPES:
            continue
        name = str(_eattr(e, 'name') or _eattr(e, 'custom_name')).lower()
        if 'rabbit' in etype or 'rabbit' in name:
            continue

        coords = _epos(e)
        if coords is None:
            continue
        ex, ey, ez = coords

        if (FARM_X_MIN <= ex <= FARM_X_MAX
                and FARM_Z_MIN <= ez <= FARM_Z_MAX
                and 50 <= ey <= 120):
            pests.append(e)

    return pests


NAV_SAFE_Y = FARM_Y + 3  # Flughöhe über dem Farm-Boden


def _fly_up(blocks: float = 3.0) -> None:
    """Fliege `blocks` Blöcke nach oben und warte bis erreicht."""
    try:
        start_y = float(player().position[1])
        target_y = start_y + blocks
        player_press_jump(True)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if float(player().position[1]) >= target_y - 0.2:
                break
            time.sleep(0.05)
        player_press_jump(False)
    except Exception:
        pass


def _lookup_pest_pos(pest_id):
    """Aktuelle Position der Pest per ID aus entities() – None wenn verschwunden."""
    try:
        for e in entities():
            if getattr(e, 'id', None) == pest_id:
                return _epos(e)
    except Exception:
        pass
    return None


def _kill_pest(pest) -> None:
    coords = _epos(pest)
    if coords is None:
        return
    tx, ty, tz = coords
    pest_id = getattr(pest, 'id', None)

    echo(f"Schaedling bei {tx:.0f} {ty:.0f} {tz:.0f} – fliege hin")

    # Navigation mit Live-Positionsupdate
    player_press_forward(True)
    deadline = time.time() + NAV_TIMEOUT
    while time.time() < deadline:
        if not RUN_FILE.exists():
            player_press_forward(False)
            _stop_all_keys()
            return
        if pest_id is not None:
            cur = _lookup_pest_pos(pest_id)
            if cur is None:
                break   # Pest verschwunden
            tx, ty, tz = cur
        try:
            p = player()
            px = float(p.position[0])
            pz = float(p.position[2])
            xz_dist = math.sqrt((tx - px) ** 2 + (tz - pz) ** 2)
            if xz_dist < NAV_CLOSE_ENOUGH:
                break
            player_look_at(tx, max(NAV_SAFE_Y, ty + 1), tz)
        except Exception:
            pass
        time.sleep(0.1)

    player_press_forward(False)
    player_press_backward(True)
    time.sleep(0.25)
    player_press_backward(False)

    if not RUN_FILE.exists():
        return

    # Auf Pest ausrichten und Vakuum benutzen
    try:
        player_look_at(tx, ty, tz)
    except Exception:
        pass

    player_press_use(True)

    deadline = time.time() + PEST_KILL_TIMEOUT
    while time.time() < deadline:
        if not RUN_FILE.exists():
            player_press_use(False)
            _stop_all_keys()
            return
        try:
            ents = entities()
            if pest_id is not None:
                alive = False
                for e in ents:
                    if getattr(e, 'id', None) == pest_id:
                        alive = True
                        c = _epos(e)
                        if c is not None:
                            tx, ty, tz = c
                            try:
                                player_look_at(tx, ty, tz)
                            except Exception:
                                pass
                        break
            else:
                alive = any(
                    str(_eattr(e, 'type')).lower().split('.')[-1] not in _SKIP_ENTITY_TYPES
                    and _epos(e) is not None
                    and abs(_epos(e)[0] - tx) < 2
                    and abs(_epos(e)[2] - tz) < 2
                    for e in ents
                )
            if not alive:
                break
        except Exception:
            pass
        time.sleep(0.1)

    player_press_use(False)
    echo("Schaedling besiegt")


def _clear_pests_in_area(rounds: int = 10) -> None:
    """Tötet alle sichtbaren Schädlinge im aktuellen Bereich (mehrere Runden)."""
    for _ in range(rounds):
        if not RUN_FILE.exists():
            return
        pests = _get_pests()
        if not pests:
            break
        echo(f"{len(pests)} Schaedling(e) – starte Bekaempfung")
        for pest in pests:
            if not RUN_FILE.exists():
                return
            _kill_pest(pest)
            time.sleep(0.3)
        time.sleep(0.5)


def _handle_pests(saved_pos: tuple, strafing_left: bool) -> None:
    echo("Schaedlinge erkannt – Farming pausiert")
    _stop_all_keys()
    time.sleep(0.2)

    # Flug aktivieren, dann hochfliegen
    _toggle_flight()
    _fly_up(3)

    try:
        _set_hotbar_slot(1)
    except Exception as err:
        echo(f"Hotbar Fehler: {err}")

    # Aktuellen Plot leeren
    _clear_pests_in_area()

    if not RUN_FILE.exists():
        return

    # Anderen Plot prüfen und leeren
    if saved_pos:
        sx = saved_pos[0]
        other = PLOT2_CHECK_POS if sx < PLOT_BOUNDARY_X else PLOT1_CHECK_POS
        echo(f"Fliege zum anderen Plot (X={other[0]:.0f})")
        _fly_to(*other)
        if RUN_FILE.exists():
            _clear_pests_in_area(rounds=3)

    if not RUN_FILE.exists():
        return

    echo("Alle Schaedlinge besiegt – kehre zur Ausgangsposition zurueck")

    try:
        _set_hotbar_slot(0)
    except Exception as err:
        echo(f"Hotbar Fehler: {err}")

    _stop_all_keys()

    # 5 Blöcke vor der Startposition stoppen, dann zu Fuß weitergehen
    sx, sy, sz = saved_pos
    _fly_to(sx, NAV_SAFE_Y, sz, stop_dist=5.0)

    if not RUN_FILE.exists():
        return

    # Flug aus, landen
    _toggle_flight()
    _wait_land()

    if not RUN_FILE.exists():
        return

    # Zu Fuß zur exakten Startposition (inkl. Y für player_look_at)
    _walk_to(sx, sy)

    if not RUN_FILE.exists():
        return

    # Ausrichtung wiederherstellen
    player_set_orientation(90, 0)
    time.sleep(0.2)

    echo("Position wiederhergestellt – Farming wird fortgesetzt")
    player_press_forward(True)
    if strafing_left:
        player_press_left(True)
    else:
        player_press_right(True)
    player_press_attack(True)


PEST_TRIGGER_COUNT = 6   # unique pests needed to trigger handling


def _ensure_watchdog() -> None:
    if PID_FILE.exists():
        try:
            os.kill(int(PID_FILE.read_text()), 0)
            echo("[Watchdog] Läuft bereits")
            return
        except (ProcessLookupError, ValueError, OSError):
            pass
    subprocess.Popen(
        [sys.executable, str(WATCHDOG_FILE)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    echo("[Watchdog] Gestartet")


def start_loop() -> None:
    RUN_FILE.write_text("running", encoding="utf-8")
    _ensure_watchdog()
    echo("=== wheat v3 start ===")
    if _HOTBAR_FUNC_NAME:
        echo(f"hotbar: {_HOTBAR_FUNC_NAME}")
    else:
        echo(f"hotbar: NICHT gefunden – kandidaten: {_HOTBAR_CANDIDATES}")

    strafing_left = True
    player_press_forward(True)
    player_press_left(True)
    player_press_right(False)
    player_press_attack(True)

    last_z_block = None
    last_z_stable_time = time.time()
    next_stable_threshold = random.uniform(STRAFE_STABLE_MIN, STRAFE_STABLE_MAX)
    WARP_X, WARP_Y, WARP_Z = -234, 70, -47

    HARVEST_TIMEOUT = 30.0
    last_harvest_time = time.time()

    # Log-basierter Pest-Counter (plotübergreifend, renderdistanz-unabhängig)
    pest_chat_count = _load_pest_count()
    echo(f"[Pest] Gespeicherter Counter: {pest_chat_count}/{PEST_TRIGGER_COUNT}")
    log_pos = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0

    _restart = False
    try:
        while RUN_FILE.exists():
            now = time.time()

            # ---- Position lesen ----
            try:
                p = player()
                x = float(p.position[0])
                y = float(p.position[1])
                z = float(p.position[2])
            except Exception:
                x = y = z = None

            # ---- Warp-Trigger bei -234 70 -47 ----
            if x is not None:
                if (abs(x - WARP_X) < 1.5 and abs(y - WARP_Y) < 1.5 and abs(z - WARP_Z) < 1.5):
                    player_press_attack(False)
                    player_press_forward(False)
                    player_press_left(False)
                    player_press_right(False)
                    chat("/warp garden")
                    time.sleep(2.0)
                    echo("Warp ausgefuehrt")
                    player_press_forward(True)
                    if strafing_left:
                        player_press_left(True)
                    else:
                        player_press_right(True)
                    player_press_attack(True)

            # ---- Harvest-Timeout ----
            try:
                targeted = player_get_targeted_block(5)
                if targeted is not None and "wheat" in str(targeted).lower():
                    last_harvest_time = now
            except Exception:
                pass

            if now - last_harvest_time >= HARVEST_TIMEOUT:
                echo("Kein Weizen seit 30s – Warp und Neustart")
                player_press_attack(False)
                player_press_forward(False)
                player_press_left(False)
                player_press_right(False)
                chat("/lobby")
                time.sleep(3.0)
                chat("/skyblock")
                time.sleep(3.0)
                chat("/warp garden")
                time.sleep(6.0)
                _restart = True
                break

            # ---- Pest-Counter via latest.log (plotübergreifend, renderdistanz-unabhängig) ----
            if LOG_FILE.exists():
                try:
                    with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as _lf:
                        _lf.seek(log_pos)
                        _new = _lf.read()
                        log_pos = _lf.tell()
                    for _line in _new.splitlines():
                        if "[CHAT]" not in _line:
                            continue
                        _chat = _line.split("[CHAT]", 1)[1].strip()
                        if _chat.startswith(_OWN_ECHO_PREFIXES):
                            continue
                        _clean = re.sub(r'§.', '', _chat)
                        if DEBUG_LOG:
                            echo(f"[LOG] {_clean}")
                        if PEST_SPAWN_SYMBOL in _clean:
                            increment = _parse_pest_increment(_chat)
                            pest_chat_count += increment
                            _save_pest_count(pest_chat_count)
                            echo(f"[Pest] +{increment} → {pest_chat_count}/{PEST_TRIGGER_COUNT}: {_clean}")
                except Exception as _e:
                    echo(f"[LOG] Fehler: {_e}")

            if pest_chat_count >= PEST_TRIGGER_COUNT:
                in_farm = (x is not None
                           and FARM_X_MIN - 30 <= x <= FARM_X_MAX + 30
                           and FARM_Z_MIN - 30 <= z <= FARM_Z_MAX + 30)
                if in_farm:
                    echo(f"Pest-Counter {pest_chat_count} >= {PEST_TRIGGER_COUNT} – starte Bekaempfung")
                    pest_chat_count = 0
                    _save_pest_count(0)
                    _handle_pests((x, y, z), strafing_left)
                    # NACH dem Hunt: alle während der Jagd geschriebenen Log-Zeilen überspringen
                    log_pos = LOG_FILE.stat().st_size if LOG_FILE.exists() else log_pos
                    last_harvest_time = time.time()
                else:
                    echo(f"[Pest] Counter {pest_chat_count} – nicht im Farm-Bereich, warte auf Rueckkehr")

            # ---- Strafing-Wechsel ----
            if z is not None and x is not None:
                z_block = math.floor(z)
                if last_z_block is None or z_block != last_z_block:
                    last_z_block = z_block
                    last_z_stable_time = now
                    next_stable_threshold = random.uniform(STRAFE_STABLE_MIN, STRAFE_STABLE_MAX)
                else:
                    if (now - last_z_stable_time) >= next_stable_threshold:
                        if strafing_left:
                            player_press_right(False)
                            player_press_left(True)
                            strafing_left = False
                        else:
                            player_press_right(True)
                            player_press_left(False)
                            strafing_left = True
                        last_z_stable_time = now
                        next_stable_threshold = random.uniform(STRAFE_STABLE_MIN, STRAFE_STABLE_MAX)

            time.sleep(0.01)

    finally:
        _save_pest_count(pest_chat_count)
        player_press_attack(False)
        player_press_forward(False)
        player_press_backward(False)
        player_press_left(False)
        player_press_right(False)
        player_press_use(False)
        if RUN_FILE.exists():
            RUN_FILE.unlink()

    if _restart:
        echo("Neustart...")
        time.sleep(2.0)
        chat("\\wheat start")


def stop_loop() -> None:
    if RUN_FILE.exists():
        RUN_FILE.unlink()
    player_press_attack(False)
    player_press_forward(False)
    player_press_backward(False)
    player_press_left(False)
    player_press_right(False)
    player_press_use(False)
    echo("Stop wheat harvesting")


def main() -> None:
    action = sys.argv[1].lower() if len(sys.argv) > 1 else ""

    if action == "start":
        if RUN_FILE.exists():
            echo("Wheat harvesting is already running")
            return
        start_loop()
        return

    if action == "stop":
        stop_loop()
        return

    echo("Usage: \\wheat start | \\wheat stop")


main()
