"""도술 상세 카드 스크린샷에서 아이콘을 추출하고 이름 색인을 만든다.

    python -m tools.extract_skill_icons

입력은 tools.dosul_scan이 만든 screenshots/dosul/01.png ... 이며,
순서는 skill_guide.UNLOCKED_SKILLS의 실측 순서와 같아야 한다.
"""
from __future__ import annotations

import json
import os

import cv2
import numpy as np

from skill_guide import UNLOCKED_SKILLS


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DIR = os.path.join(ROOT, "screenshots", "dosul")
OUTPUT_DIR = os.path.join(ROOT, "assets", "skills")

# 1920×1080에서 저장된 548×381 상세 카드 안의 아이콘 경계.
ICON_X, ICON_Y, ICON_SIZE = 23, 5, 84
OUTPUT_SIZE = 64

# 이전 실측에서 확보했지만 현재 40장 스캔 뒤쪽에서 누락된 아이콘.
# 도술 목록에는 잠긴 카드 뒤에도 습득 카드가 이어질 수 있다.
SUPPLEMENTAL_ICONS = {
    "요혼격": "41.png",
    "날카로운 얼음창": "42.png",
    "붕괴하는 대지": "43.png",
    "번개 폭풍": "44.png",
    "대폭발": "45.png",
}


def rounded_alpha(size: int, radius: int) -> np.ndarray:
    """게임 아이콘의 둥근 모서리 밖만 투명하게 만든다."""
    mask = np.zeros((size, size), dtype=np.uint8)
    cv2.rectangle(mask, (radius, 0), (size - radius - 1, size - 1), 255, -1)
    cv2.rectangle(mask, (0, radius), (size - 1, size - radius - 1), 255, -1)
    for x, y in ((radius, radius), (size - radius - 1, radius),
                 (radius, size - radius - 1), (size - radius - 1, size - radius - 1)):
        cv2.circle(mask, (x, y), radius, 255, -1, cv2.LINE_AA)
    return mask


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    index = {}
    alpha = rounded_alpha(OUTPUT_SIZE, 7)
    missing = []

    for number, row in enumerate(UNLOCKED_SKILLS, 1):
        name = row[0]
        source = os.path.join(SOURCE_DIR, f"{number:02d}.png")
        image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR) if os.path.exists(source) else None
        if image is None:
            missing.append(source)
            continue
        icon = image[ICON_Y:ICON_Y + ICON_SIZE, ICON_X:ICON_X + ICON_SIZE]
        if icon.shape[:2] != (ICON_SIZE, ICON_SIZE):
            raise RuntimeError(f"아이콘 영역 크기 오류: {source} = {icon.shape}")
        icon = cv2.resize(icon, (OUTPUT_SIZE, OUTPUT_SIZE), interpolation=cv2.INTER_AREA)
        bgra = cv2.cvtColor(icon, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = alpha
        filename = f"{number:02d}.png"
        cv2.imencode(".png", bgra)[1].tofile(os.path.join(OUTPUT_DIR, filename))
        index[name] = filename

    if missing:
        raise FileNotFoundError("누락된 도술 카드:\n" + "\n".join(missing))
    if len(index) != len(UNLOCKED_SKILLS):
        raise RuntimeError(f"도술 이름 중복: {len(index)}/{len(UNLOCKED_SKILLS)}")

    for name, filename in SUPPLEMENTAL_ICONS.items():
        path = os.path.join(OUTPUT_DIR, filename)
        if not os.path.exists(path):
            raise FileNotFoundError(f"보조 도술 아이콘 누락: {path}")
        index[name] = filename

    with open(os.path.join(OUTPUT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"완료: 현재 스캔 40개 + 기존 실측 {len(SUPPLEMENTAL_ICONS)}개 → {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
