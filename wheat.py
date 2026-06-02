import sys
import time
import math
import random
from pathlib import Path

from minescript import (
	chat,
	echo,
	player_press_attack,
	player_press_jump,
	player,
	player_get_targeted_block,
	player_press_forward,
	player_press_backward,
	player_press_left,
	player_press_right,
)

RUN_FILE = Path(__file__).with_suffix(".running")
STRAFE_STABLE_MIN = 1
STRAFE_STABLE_MAX = 1.1

def start_loop() -> None:
	RUN_FILE.write_text("running", encoding="utf-8")
	echo("Start wheat harvesting")

	# Movement state: always hold forward, alternate left/right strafing
	strafing_left = True
	player_press_forward(True)
	player_press_left(True)
	player_press_right(False)
	player_press_attack(True)

	# Z-block tracking for strafing switch
	last_z_block = None
	last_z_stable_time = time.time()
	next_stable_threshold = random.uniform(STRAFE_STABLE_MIN, STRAFE_STABLE_MAX)
	WARP_X, WARP_Y, WARP_Z = -234, 70, -47

	# Harvest timeout: wenn 30s kein Weizenblock anvisiert -> /skyblock + /warp garden
	HARVEST_TIMEOUT = 30.0
	last_harvest_time = time.time()

	# Flug-Erkennung via Geschwindigkeit (Fliegen ~10.9 Bl/s, Laufen ~6.5 Bl/s @ Speed 150)
	FLIGHT_SPEED_THRESHOLD = 8.0
	FLIGHT_COOLDOWN = 3.0
	last_speed_pos = None
	last_speed_time = time.time()
	last_flight_disable = 0.0

	try:
		while RUN_FILE.exists():
			now = time.time()
			# ---- Movement handling ----
			try:
				p = player()
				x = float(p.position[0])
				y = float(p.position[1])
				z = float(p.position[2])
			except Exception:
				x = y = z = None

			# Flug-Erkennung: Geschwindigkeit messen, bei Überschreitung doppelt Space drücken
			if x is not None:
				dt = now - last_speed_time
				if dt >= 0.1:
					if last_speed_pos is not None:
						dx = x - last_speed_pos[0]
						dy = y - last_speed_pos[1]
						dz = z - last_speed_pos[2]
						speed = math.sqrt(dx*dx + dy*dy + dz*dz) / dt
						if speed > FLIGHT_SPEED_THRESHOLD and now - last_flight_disable > FLIGHT_COOLDOWN:
							echo("Fliegen erkannt – Flug wird deaktiviert")
							player_press_jump(True)
							time.sleep(0.1)
							player_press_jump(False)
							time.sleep(0.1)
							player_press_jump(True)
							time.sleep(0.1)
							player_press_jump(False)
							last_flight_disable = now
					last_speed_pos = (x, y, z)
					last_speed_time = now

			# Warp trigger bei Koordinate -234 70 -47s
			if x is not None:
				if (abs(x - WARP_X) < 1.5 and abs(y - WARP_Y) < 1.5 and abs(z - WARP_Z) < 1.5):
					player_press_attack(False)
					player_press_forward(False)
					player_press_left(False)
					player_press_right(False)
					chat("/warp garden")
					time.sleep(2.0)
					last_speed_pos = None
					echo("Warp ausgefÃ¼hrt")
					player_press_forward(True)
					if strafing_left:
						player_press_left(True)
					else:
						player_press_right(True)
					player_press_attack(True)
					echo("Weiter mit Harvesting")

			# Harvest-Timeout: Weizenblock anvisiert -> Timer zurücksetzen
			try:
				targeted = player_get_targeted_block(5)
				if targeted is not None and "wheat" in str(targeted).lower():
					last_harvest_time = now
			except Exception:
				pass

			if now - last_harvest_time >= HARVEST_TIMEOUT:
				echo("Kein Weizen seit 30s – Warp wird ausgefÃ¼hrt")
				player_press_attack(False)
				player_press_forward(False)
				player_press_left(False)
				player_press_right(False)
				chat("/skyblock")
				time.sleep(3.0)
				chat("/warp garden")
				time.sleep(2.0)
				last_harvest_time = time.time()
				last_speed_pos = None
				player_press_forward(True)
				if strafing_left:
					player_press_left(True)
				else:
					player_press_right(True)
				player_press_attack(True)

			if z is not None and x is not None:
				z_block = math.floor(z)
				if last_z_block is None or z_block != last_z_block:
					last_z_block = z_block
					last_z_stable_time = now
					# pick a new threshold for the next stable period
					next_stable_threshold = random.uniform(STRAFE_STABLE_MIN, STRAFE_STABLE_MAX)
				else:
					# still in same z-block
					if (now - last_z_stable_time) >= next_stable_threshold:
						# switch strafing side
						if strafing_left:
							player_press_right(False)
							player_press_left(True)
							strafing_left = False
						else:
							player_press_right(True)
							player_press_left(False)
							strafing_left = True

						# reset stable timer and pick next threshold
						last_z_stable_time = now
						next_stable_threshold = random.uniform(STRAFE_STABLE_MIN, STRAFE_STABLE_MAX)	
									
			time.sleep(0.01)
			
	finally:
		# cleanup: release keys
		player_press_attack(False)
		player_press_forward(False)
		player_press_backward(False)
		player_press_left(False)
		player_press_right(False)
		if RUN_FILE.exists():
			RUN_FILE.unlink()


def stop_loop() -> None:
	if RUN_FILE.exists():
		RUN_FILE.unlink()
	player_press_attack(False)
	player_press_forward(False)
	player_press_backward(False)
	player_press_left(False)
	player_press_right(False)
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