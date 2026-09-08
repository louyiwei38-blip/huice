"""Long-running live process with auto-restart. Use this on a server."""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from range_mr.config import Config
from range_mr.live import run_live

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "live.log"


def log(msg: str) -> None:
    ts = datetime.now(tz=ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    delay = 5
    while True:
        log("启动 live：BTCUSDT + ETHUSDT，信号推 Telegram")
        try:
            run_live(Config())
            log("live 正常退出，5 秒后重启")
        except KeyboardInterrupt:
            log("手动停止")
            return
        except Exception:
            log("live 异常退出:\n" + traceback.format_exc())
        time.sleep(delay)


if __name__ == "__main__":
    main()
