"""스크린샷에서 버튼 영역을 드래그해 templates/ 에 저장하고 비율 좌표를 출력.

    python -m tools.crop screenshots\\20261012_130000.png

드래그 → Enter(또는 Space)로 확정 → 콘솔에 파일 이름 입력.
아무것도 선택하지 않고 Enter 또는 C 를 누르면 종료.
"""
import os
import sys

import cv2

from bot.vision import load_image, save_image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(ROOT, "templates")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    image = load_image(sys.argv[1])
    if image is None:
        print("이미지를 읽을 수 없습니다:", sys.argv[1])
        return
    h, w = image.shape[:2]
    os.makedirs(TEMPLATE_DIR, exist_ok=True)

    win = "crop - drag, Enter to confirm, C to quit"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    while True:
        x, y, cw, ch = cv2.selectROI(win, image, showCrosshair=True)
        if cw == 0 or ch == 0:
            break
        print(f"\n선택 영역 비율 [x, y, w, h] = [{x / w:.3f}, {y / h:.3f}, {cw / w:.3f}, {ch / h:.3f}]")
        print(f"중심 클릭 비율 [x, y]      = [{(x + cw / 2) / w:.3f}, {(y + ch / 2) / h:.3f}]")
        name = input("템플릿 파일 이름 (예: reward.png, 빈칸이면 저장 안 함): ").strip()
        if name:
            if not name.lower().endswith(".png"):
                name += ".png"
            path = os.path.join(TEMPLATE_DIR, name)
            save_image(path, image[y:y + ch, x:x + cw])
            print("저장:", path)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
