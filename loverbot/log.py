"""loverbot 统一日志：控制台 + 数据目录滚动文件。"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("loverbot")


def setup_logging(data_dir: Path, debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    logger.setLevel(level)
    if logger.handlers:
        return

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "loverbot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # 降低第三方库噪音
    for noisy in ("httpx", "telegram", "apscheduler", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
