"""퀘스트 자동 진행 매크로 실행 진입점.

    python main.py              # 실행
    python main.py --dry-run    # 클릭하지 않고 감지 결과만 로그로 확인
    python main.py --debug      # 멈춤 시간/화면 변화량까지 출력
"""
import argparse
import logging
import logging.handlers
import os
import sys

import yaml

from bot2.brain import Bot
from bot.window import enable_dpi_awareness

from app_paths import ROOT, initialize_data


def setup_logging(debug):
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.handlers.RotatingFileHandler(os.path.join(ROOT, "logs", "bot2.log"), maxBytes=5_000_000,
                                                 backupCount=5, encoding="utf-8"),
        ],
    )


def main():
    initialize_data()
    parser = argparse.ArgumentParser(description="퀘스트 자동 진행 매크로")
    parser.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    parser.add_argument("--dry-run", action="store_true", help="클릭/키 입력 없이 로그만")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--farm", action="store_true", help="사냥터 고정 모드 (퀘스트 대신 전투력에 맞는 사냥터에서 자동 사냥)")
    parser.add_argument("--normal", action="store_true", help="일반 모드 (현재 위치에서 자동 사냥, 저체력 피신 후 복귀)")
    parser.add_argument("--daily", action="store_true", help="일일 임무 전용 모드 (본편·서사·주간 제외)")
    parser.add_argument("--story", action="store_true", help="서사 임무 전용 모드 (현재 전투력 적합 임무만)")
    args = parser.parse_args()

    setup_logging(args.debug)
    enable_dpi_awareness()
    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if args.farm:
        config.setdefault("farm", {})["enabled"] = True
        config.setdefault("normal_mode", {})["enabled"] = False
        config.setdefault("daily_mode", {})["enabled"] = False
        config.setdefault("story_mode", {})["enabled"] = False
    if args.normal:
        config.setdefault("normal_mode", {})["enabled"] = True
        config.setdefault("farm", {})["enabled"] = False
        config.setdefault("daily_mode", {})["enabled"] = False
        config.setdefault("story_mode", {})["enabled"] = False
    if args.daily:
        config.setdefault("daily_mode", {})["enabled"] = True
        config.setdefault("normal_mode", {})["enabled"] = False
        config.setdefault("farm", {})["enabled"] = False
        config.setdefault("story_mode", {})["enabled"] = False
    if args.story:
        config.setdefault("story_mode", {})["enabled"] = True
        config.setdefault("daily_mode", {})["enabled"] = False
        config.setdefault("normal_mode", {})["enabled"] = False
        config.setdefault("farm", {})["enabled"] = False
    from execution_lock import acquire_bot_lock
    execution_lock = acquire_bot_lock(config)
    bot = None
    try:
        bot = Bot(config, dry_run=args.dry_run)
        bot.run()
    except KeyboardInterrupt:
        logging.info("Ctrl+C로 종료")
    finally:
        if bot:
            bot.stop_event.set()
            bot.notifier.stop()
            bot.live.stop()
        execution_lock.unlock()


if __name__ == "__main__":
    main()
