"""loverbot 入口：python main.py [-c config.yaml]"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from loverbot.app import App
from loverbot.config import Cfg
from loverbot.log import logger, setup_logging


def parse_args():
    parser = argparse.ArgumentParser(description="loverbot —— 拟真 AI 恋人")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    return parser.parse_args()


async def _run(app: App):
    stop = asyncio.Event()

    def _request_stop(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # Windows
            signal.signal(sig, _request_stop)

    await app.initialize()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await app.terminate()


def main():
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"找不到配置文件：{config_path}")
        print("请复制 config.example.yaml 为 config.yaml 并填写。")
        sys.exit(1)

    cfg = Cfg.load(config_path)
    setup_logging(cfg.data_dir, cfg.debug)

    missing = cfg.missing_required()
    if missing:
        logger.error("[loverbot] 缺少必需配置：" + "；".join(missing))
        sys.exit(1)

    try:
        asyncio.run(_run(App(cfg)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
