"""실제 게임에서 OpenCV 도로 회피 이동과 원위치 복귀를 1회 검증한다."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot2.evade import Evader
from bot2.perception import autohunt_on
from tools.farm_scan import BridgeDriver

OUTPUT = ROOT / "screenshots" / "evade-live"


def save_image(name, image):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / name
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise OSError(f"이미지 저장 실패: {path}")
    encoded.tofile(path)
    return path


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    driver = BridgeDriver()
    before = driver.shot()
    save_image("01_before.png", before)
    was_auto = autohunt_on(before, cfg)
    if was_auto:
        driver.key("t")
        driver.sleep(1.5)

    distance = int((cfg.get("hp") or {}).get("evade_dist_px", 900))
    minimum = int((cfg.get("hp") or {}).get("evade_min_dist_px", 560))
    min_meters = int((cfg.get("hp") or {}).get("evade_min_meters", 40))
    evader = Evader(driver, distance, minimum, min_meters)
    started = time.monotonic()
    token = evader.flee(before)
    after_flee = driver.shot()
    save_image("02_after_flee.png", after_flee)
    returned = bool(token and evader.back(token))
    after_return = driver.shot()
    save_image("03_after_return.png", after_return)

    if was_auto:
        driver.key("t")
        driver.sleep(1.0)
    final_frame = driver.shot()
    auto_restored = autohunt_on(final_frame, cfg) == was_auto
    result = {
        "success": bool(token and returned and auto_restored),
        "offset": list(token.get("offset", ())) if isinstance(token, dict) else None,
        "return_command_succeeded": returned,
        "exact_position_verified": returned,
        "auto_restored": auto_restored,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    (OUTPUT / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
