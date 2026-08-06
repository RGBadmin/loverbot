"""loverbot 入口：python main.py [-c config/config.yaml]

设计理念：先跑起来。配置不全也能启动——Web 面板永远可用，
缺什么在面板「配置」页补什么，保存并应用即可，无需重启进程。
"""

import argparse
import asyncio
import shutil
import signal
from pathlib import Path

from loverbot.app import App
from loverbot.config import Cfg
from loverbot.log import logger, setup_logging

_EXAMPLE = Path(__file__).resolve().parent / "config.example.yaml"


def parse_args():
    parser = argparse.ArgumentParser(description="loverbot —— 拟真 AI 恋人")
    parser.add_argument("-c", "--config", default="config/config.yaml", help="配置文件路径")
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
        config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_EXAMPLE, config_path)
        print(f"已生成配置模板：{config_path}（可直接启动，稍后在 Web 面板里填写）")

    cfg = Cfg.load(config_path)
    setup_logging(cfg.data_dir, cfg.debug)

    missing = cfg.missing_required()
    if missing:
        logger.warning(
            "[loverbot] 以下配置尚未填写，相关功能暂不启用（Web 面板「配置」页可补）："
            + "；".join(missing)
        )

    try:
        asyncio.run(_run(App(cfg, config_path)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
