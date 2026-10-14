"""화첩 수집 계획: 아이템 사전 조사(item_dict.json)로 '다음에 어느 몬스터를 잡으러 갈지'를 정한다.

- 그림 목록·순서·몬스터(이름/지역/전투력)는 item_dict.json 의 '화첩 조각' 아이템에서 가져온다 (목록 순서 = 화첩 순서).
  매번 그림 안으로 들어가 몬스터 목록을 읽어 고르지 않는다.
- 완료한 그림은 collect_done.json 에 따로 저장한다 (한 번 완료로 기록하면 지우지 않는다).
- 순서: 책 순서대로 위→아래·왼쪽→오른쪽(= 목록 순서)으로 미완료 그림 중 앞에서부터, 그림 안에서는 가장 약한 몬스터.
"""
import json
import os
import re
import time
from difflib import SequenceMatcher

from .farm import ROOT

DONE_FILE = os.path.join(ROOT, "collect_done.json")
DICT_FILE = os.path.join(ROOT, "item_dict.json")
CATEGORY = "화첩 조각"


def title_of(item_name):
    """'호랑이 화첩 조각' → '호랑이' (OCR이 '화점'·'호법'으로 읽은 것도 같이 뗀다)."""
    t = re.sub(r"\s*(화\S{0,1}|호법)?\s*조각\s*$", "", item_name or "").strip()
    t = re.sub(r"^[^가-힣A-Za-z]+", "", t)
    return t.strip()


def _ratio(a, b):
    a, b = re.sub(r"\s", "", a or ""), re.sub(r"\s", "", b or "")
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_done(path=DONE_FILE):
    return load_json(path, {"paintings": {}}).get("paintings", {})


def save_done(done, path=DONE_FILE):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"paintings": done}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def paintings_from_dict(idict):
    """item_dict.json → [{title, item, order, sources, monsters}] (화첩 조각 아이템, 목록 순서, 같은 그림이 두 번 잡힌 것은 하나로)."""
    out = []
    for it in idict.get("items", []):
        if it.get("category") != CATEGORY:
            continue
        title = title_of(it.get("name", ""))
        if len(title) < 1:
            continue
        dup = next((p for p in out if _ratio(p["title"], title) >= 0.85 and p["sources"] == it.get("sources")), None)
        if dup:
            if len(it.get("monsters", [])) > len(dup["monsters"]):
                dup["monsters"] = it["monsters"]
            continue
        out.append({"title": title, "item": it.get("name", ""), "order": len(out),
                    "sources": it.get("sources", []), "monsters": it.get("monsters", [])})
    return out


def match_painting(paintings, name, min_ratio=0.75):
    """화첩 그림 제목(name)에 해당하는 그림. 못 찾으면 None. 한 글자 제목은 같은 글자일 때만."""
    name = re.sub(r"\s", "", name or "")
    if len(name) < 2 or "?" in name:
        return None
    best, score = None, 0.0
    for p in paintings:
        r = _ratio(p["title"], name)
        if r > score:
            best, score = p, r
    return best if score >= min_ratio else None


def _known(name):
    n = re.sub(r"\s", "", name or "")
    return len(n) >= 2 and "?" not in n and re.fullmatch(r"[가-힣A-Za-z]+", n) is not None


def align(paintings, album):
    """화첩 스캔(album)과 아이템 사전(paintings)은 같은 순서(책 순서)지만 양쪽 이름에 OCR 오독('?', 깨진 글자)이 있어
    이름만으로는 짝을 못 짓는다 → 순서를 지키는 정렬(DP)로 짝을 짓는다. {album 번호: painting 번호}."""
    A, P = len(album), len(paintings)
    GAP = -0.5

    def sim(i, j):
        a, b = album[i].get("name"), paintings[j]["title"]
        if not _known(a) or not _known(b):
            return 0.3                                  # 이름을 못 읽은 쪽은 순서만 믿는다
        r = _ratio(a, b)
        return 2.0 * r if r >= 0.5 else -0.6           # 이름이 크게 달라도 양쪽 앞뒤가 맞으면 '한 글자도 못 맞게 읽은 같은 그림'일 수 있어 두 번 건너뛰는(-1.0) 것보다 낫게 둔다

    dp = [[0.0] * (P + 1) for _ in range(A + 1)]
    for i in range(1, A + 1):
        dp[i][0] = i * GAP
    for j in range(1, P + 1):
        dp[0][j] = j * GAP
    for i in range(1, A + 1):
        for j in range(1, P + 1):
            dp[i][j] = max(dp[i - 1][j - 1] + sim(i - 1, j - 1), dp[i - 1][j] + GAP, dp[i][j - 1] + GAP)
    out, i, j = {}, A, P
    while i > 0 and j > 0:
        if abs(dp[i][j] - (dp[i - 1][j - 1] + sim(i - 1, j - 1))) < 1e-9:
            out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif abs(dp[i][j] - (dp[i - 1][j] + GAP)) < 1e-9:
            i -= 1
        else:
            j -= 1
    return out


def sync_done(done, paintings, album, now=None):
    """화첩 스캔 결과(album 항목의 done)에서 완료된 그림을 완료 기록에 더한다. 새로 더한 수."""
    pairs = align(paintings, album or [])
    n = 0
    for i, e in enumerate(album or []):
        if not e.get("done") or i not in pairs:
            continue
        title = paintings[pairs[i]]["title"]
        if title not in done:
            done[title] = now or time.time()
            n += 1
    return n


def build_candidates(paintings, done, power, max_ratio=2.0, banned=(), done_round=()):
    """[(전투력, -1, 몬스터, ("dict", 그림))] — 그림 순서대로, 그림마다 가장 약한 몬스터 하나.
    요귀 획득처가 없는 그림(콘텐츠·임무·상자)과 완료한 그림, 전투력 상한(power*max_ratio)을 넘는 몬스터만 있는 그림은 건너뛴다."""
    if not power:
        return []
    skip = set(banned) | set(done_round)
    out = []
    for p in paintings:
        if p["title"] in done or "요귀" not in p["sources"]:
            continue
        ok = [m for m in p["monsters"]
              if m.get("power") and m["power"] <= power * max_ratio and m.get("name")
              and (m["name"], m.get("region", "")) not in skip]
        if not ok:
            continue
        m = min(ok, key=lambda x: x["power"])
        out.append((m["power"], -1, m, ("dict", p)))
    return out
