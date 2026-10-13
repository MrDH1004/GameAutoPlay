"""시작 점검: 프로그램을 켜면 먼저 화면을 한 번 찍어 상태부터 파악한다 (읽기만 하고 아무것도 누르지 않는다).

읽는 것: 화면 종류(필드/사망/기타), 지금 있는 지도 이름, HP, 전투력, 물약 개수, 자동 사냥 켜짐 여부,
        오른쪽 퀘스트 트래커에 보이는 퀘스트 줄 수(본편/서사/외전…).
결과는 로그로 남기고 제어판에 표시한다. 위험 신호(물약 부족, HP 낮음, 위험 지역)는 경고한다.
"""
import logging
import re
import time

from .farm import _binarize, _crop, _ocr, read_power
from .safety import hp_ratio
from .shop import read_potion_count

log = logging.getLogger(__name__)

MAP_NAME_BOX = (20, 12, 300, 44)            # 좌상단 지도 이름 (1920x1080 기준)
TRACKER_BOX = (1540, 105, 1830, 520)        # 오른쪽 퀘스트 트래커


def read_map_name(frame):
    """지금 있는 지도 이름 ('안개 기슭 지하 동굴', '우물동굴' …). 못 읽으면 ''."""
    crop = _crop(frame, MAP_NAME_BOX)
    best = ""
    for img, sc in ((crop, 3), (crop, 2), (_binarize(crop, 180), 3)):
        txt = _ocr(img, sc).strip()
        txt = re.sub(r"^[\d\W_]+(?=[가-힣\[])", "", txt)             # 앞의 방패 아이콘이 숫자/기호로 읽힌 것을 뗀다 ('9 안개…')
        txt = re.sub(r"^[\d]\s+", "", txt)
        txt = re.sub(r"\s+", " ", txt)
        if len(txt) > len(best):
            best = txt
        if len(best) >= 3 and re.search(r"[가-힣]{2,}", best):
            break
    return best


def count_tracker_lines(frame):
    """트래커에 보이는 퀘스트 제목 줄 수. 본편([N장])과 그 외로 나눈다. (스크롤하지 않고 보이는 것만)"""
    try:
        from tools.ocr import read_text
        crop = _crop(frame, TRACKER_BOX)
        lines = read_text(crop, scale=2)
    except Exception as e:  # noqa: BLE001
        log.debug("트래커 읽기 실패: %s", e)
        return None
    main = other = 0
    for ln in lines:
        t = ln["text"]
        if re.search(r"\[\s*\d+\s*장\s*\]|\d\s*장\s*\]", t):
            main += 1
        elif re.search(r"\[[^\]]{1,6}\]", t):
            other += 1
    return {"main": main, "other": other, "total": main + other}


def classify_screen(bot, frame):
    """'필드' / '사망' / '기타 화면' 중 하나."""
    try:
        if bot.matcher.find(frame, bot.cfg["death"]["template"], bot.cfg["death"].get("region")):
            return "사망 화면"
        if bot._in_field(frame):
            return "필드"
    except Exception:  # noqa: BLE001
        pass
    return "기타 화면(메뉴/던전/컷신 등)"


def check(bot):
    """화면을 한 번 찍어 상태를 읽고 dict로 돌려준다. 아무 입력도 보내지 않는다."""
    t0 = time.monotonic()
    frame = bot.window.grab()
    st = {"screen": classify_screen(bot, frame)}
    st["map"] = read_map_name(frame) if st["screen"] == "필드" else ""
    st["hp"] = round(hp_ratio(frame), 2) if st["screen"] == "필드" else None
    st["power"] = read_power(frame) if st["screen"] == "필드" else None
    st["potions"] = read_potion_count(frame) if st["screen"] == "필드" else None
    st["quests"] = count_tracker_lines(frame) if st["screen"] == "필드" else None
    try:
        st["auto_on"] = bool(bot.eyes.look(frame, time.monotonic()).auto_on)
    except Exception:  # noqa: BLE001
        st["auto_on"] = None
    st["took"] = round(time.monotonic() - t0, 1)
    return st


def describe(st):
    q = st.get("quests")
    parts = [f"화면 {st['screen']}"]
    if st["screen"] == "필드":
        parts.append(f"지도 '{st['map'] or '?'}'")
        parts.append("HP %s" % (f"{st['hp'] * 100:.0f}%" if st.get("hp") else "?"))
        parts.append("전투력 %s" % (f"{st['power']:,}" if st.get("power") else "?"))
        parts.append("물약 %s개" % (st["potions"] if st.get("potions") is not None else "?"))
        if q:
            parts.append(f"퀘스트 {q['total']}개(본편 {q['main']}, 기타 {q['other']})")
        else:
            parts.append("퀘스트 ?")
        parts.append("자동 사냥 " + ("켜짐" if st.get("auto_on") else "꺼짐" if st.get("auto_on") is False else "?"))
    return " · ".join(parts)


def warnings(st, below_potion=50):
    """점검 결과에서 눈여겨볼 위험 신호 목록."""
    out = []
    if st["screen"] != "필드":
        out.append(f"필드가 아님 ({st['screen']}) — 먼저 필드로 돌아가야 시작할 수 있음")
        return out
    if st.get("potions") is not None and st["potions"] < below_potion:
        out.append(f"물약 {st['potions']}개 (< {below_potion}) — 곧 자동 구매 대상")
    if st.get("hp") and st["hp"] < 0.5:
        out.append(f"HP {st['hp'] * 100:.0f}% — 낮은 상태로 시작")
    if st.get("potions") == 0:
        out.append("물약이 없음")
    return out
