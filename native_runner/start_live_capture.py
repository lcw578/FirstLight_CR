"""Start one authorized simulator match and collect passive live diagnostics.

This helper deliberately performs only the lobby matchmaking tap. It never
selects a card, injects a command, or calls the native action paths.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from native_runner.cr_native_env import NativeClashEnv


def adb(adb_path: str, serial: str, *args: str) -> str:
    completed = subprocess.run(
        [adb_path, "-s", serial, *args],
        check=True,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="D:/AndroidSDK/platform-tools/adb.exe")
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--x", type=int, default=540, help="Lobby battle button X coordinate")
    parser.add_argument("--y", type=int, default=1500, help="Lobby battle button Y coordinate")
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--output", type=Path, default=Path("runs/live_capture_latest.jsonl"))
    args = parser.parse_args()

    client = NativeClashEnv(host=args.host, port=args.port, timeout=5.0)
    # Start each capture from a clean passive state. This does not alter game
    # state; it only clears probe-side diagnostics and arms read-only capture.
    client.set_live_observation(False)
    client.set_live_observation(True)

    # The only simulator input emitted by this script: tap the lobby's central
    # "对战" button. No card coordinates or native action commands are used.
    adb(args.adb, args.serial, "shell", "input", "tap", str(args.x), str(args.y))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with args.output.open("w", encoding="utf-8") as stream:
        while time.monotonic() - start < args.seconds:
            diagnostic = client.live_root_diagnostic()
            players = None
            if (
                diagnostic.get("generation", 0) > 0
                and diagnostic.get("objectManager") != "0x0"
                and diagnostic.get("objectManagerGeneration") == diagnostic.get("generation")
            ):
                players = client.live_players()
            row = {"time": time.time(), "diagnostic": diagnostic, "players": players}
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            valid_players = players and all(item.get("valid") for item in players.get("players", []))
            print(json.dumps({"diagnostic": diagnostic, "players": players}, ensure_ascii=False), flush=True)
            if valid_players:
                print("VALID_TWO_PLAYER_ROOTS", flush=True)
                return 0
            time.sleep(0.1)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
