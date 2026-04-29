"""Внешний watchdog: проверяет, что main.py обновляет logs/heartbeat файл."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEARTBEAT = ROOT / "logs" / "heartbeat.txt"
INTERVAL = 300  # 5 минут
STALE_AFTER = 600  # 10 минут без heartbeat → перезапуск


def main() -> None:
    while True:
        try:
            if not HEARTBEAT.exists() or (time.time() - HEARTBEAT.stat().st_mtime) > STALE_AFTER:
                print("[watchdog] heartbeat stale, restarting service…", file=sys.stderr)
                # под NSSM сервис называется SchoolBot. Перезапуск через nssm restart.
                subprocess.call(["nssm", "restart", "SchoolBot"])
            time.sleep(INTERVAL)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
