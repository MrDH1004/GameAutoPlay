"""깨비지지 공개 도술 데이터를 이용한 기본 계수 CPU 조합 탐색기."""
from __future__ import annotations

import heapq
import io
import itertools
import json
import math
import os
import re
import time
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin


ROOT = Path(__file__).resolve().parent
CATALOG_URL = "https://kkaebigg.com/data/skills.json"
RECOMMEND_URL = "https://kkaebigg.com/data/deck_rec.json"
BUILDS_URL = "https://kkaebigg.com/guilds/api/deck-builds?page={}"
CATALOG_FILE = ROOT / "assets" / "skills" / "catalog.json"
RESULT_FILE = ROOT / "assets" / "skills" / "simulation_results.json"
SETTINGS_FILE = ROOT / "gui_settings.json"
SLOTS = 8
CORE_SLOTS = 5
HEAL_SKILL = "생명의 축복"
AOE_HIT_FACTORS = {
    # 최대 대상 수만 곱하면 전방 판정을 원형/흡인 광역처럼 과대평가한다.
    # 사용자 실전 확인: 자동 사냥 중 몬스터가 옆·뒤로 퍼지면 실제 적중 수가 줄어든다.
    "대검 강타": 0.50,
    "검기 날리기": 0.60,
}
STYLE_RANGES = {
    "절명": 1.6, "연타": 1.8, "강타": 2.2, "척살": 2.2,
    "저격": 8.0, "주술": 6.0, "파멸": 6.0, "강인": 1.6,
    "기동": 2.5, "헌신": 5.0, "기만": 5.5, "제압": 4.0,
}

ALIASES = {
    ("절명", "내려찍기"): "내려찍기(절명)",
    ("강타", "내려치기"): "내려치기(강타)",
}


def _get_json(url, retries=3):
    request = Request(url, headers={"User-Agent": "GameAutoPlay skill catalog/1.0", "Accept": "application/json"})
    error = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310 - 고정된 공개 HTTPS 주소만 사용
                return json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, ValueError) as exc:
            error = exc
            if attempt + 1 < retries:
                time.sleep(1 + attempt)
    raise error


def _targets(description):
    values = [int(value) for value in re.findall(r"(?:최대|범위[^0-9]{0,12})(\d+)\s*(?:명|인)", description or "")]
    return max(values, default=1)


def _meters(value):
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group()) if match else None


def _aoe_reach_factor(skill):
    """사냥 중 선공 범위와 몹몰이 능력을 광역 실적중 기대값으로 환산한다."""
    if int(skill.get("targets", 1)) <= 1:
        return 1.0
    reach = float(skill.get("range") or STYLE_RANGES.get(skill.get("style"), 4.0))
    radius = float(skill.get("radius") or 0.0)
    effective = max(reach, radius)
    factor = 0.82 + min(8.0, effective) * 0.045  # 2m 0.91 / 6m 1.09 / 8m 1.18
    if skill.get("mobility"):
        factor *= 1.04
    return min(1.38, factor)


def _pack_gather_factor(combo, mode):
    """흡인으로 흩어진 몹을 모아 후속 광역 전체가 맞는 실전 이득."""
    if mode != "aoe":
        return 1.0
    pulls = sum(bool(skill.get("pull")) for skill in combo)
    return 1.0 + min(0.14, pulls * 0.10)


def range_profile(catalog, names):
    """추천 덱의 사냥 유효거리와 몹몰이 구성을 화면 표시용으로 요약한다."""
    by_name = {skill["name"]: skill for skill in catalog.get("skills", [])}
    rows = [by_name[name] for name in names if name in by_name]
    aoe = [skill for skill in rows if int(skill.get("targets", 1)) > 1]
    if not aoe:
        return {"average": 0.0, "maximum": 0.0, "long_aoe": 0, "pull_skills": [], "score": 0.0}
    distances = [max(float(skill.get("range") or STYLE_RANGES.get(skill.get("style"), 4.0)),
                     float(skill.get("radius") or 0.0)) for skill in aoe]
    pulls = [skill["name"] for skill in aoe if skill.get("pull")]
    factors = [_aoe_reach_factor(skill) for skill in aoe]
    gather = _pack_gather_factor(rows, "aoe")
    return {
        "average": round(sum(distances) / len(distances), 1),
        "maximum": round(max(distances), 1),
        "long_aoe": sum(distance >= 5.0 for distance in distances),
        "pull_skills": pulls,
        "score": round(sum(factors) / len(factors) * gather * 100.0, 1),
    }


def collect_catalog():
    raw = _get_json(CATALOG_URL)
    recommend = _get_json(RECOMMEND_URL)
    try:
        previous = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    style_rows = raw["styles"]
    styles = [row["n"] for row in style_rows]
    skills = []
    known_targets = {}
    known_ranges = {}
    try:
        import skill_guide
        for item in itertools.chain(skill_guide.UNLOCKED_SKILLS, skill_guide.NEWLY_LEARNED_SKILLS):
            match = re.search(r"(\d+)\s*인", str(item[2]))
            if match:
                known_targets[item[0]] = int(match.group(1))
            measured_range = _meters(item[5]) if len(item) > 5 else None
            if measured_range is not None:
                known_ranges[item[0]] = measured_range
    except (ImportError, AttributeError):
        pass
    tags = recommend.get("tags") or {}
    for row in raw["skills"]:
        nums = row.get("num") or {}
        style = styles[int(row["st"])]
        name = ALIASES.get((style, row["n"]), row["n"])
        effects = " ".join(
            str(line) for effect in row.get("hit", []) for line in effect.get("l", []))
        effect_details = [{"type": str(effect.get("t") or ""),
                           "name": str(effect.get("n") or ""),
                           "lines": [str(line) for line in effect.get("l", [])]}
                          for effect in row.get("hit", [])]
        roles = (tags.get(str(row["id"])) or {}).get("ro", [])
        targets = known_targets.get(name, 5 if "광역 딜" in roles else _targets(row.get("d", "")))
        combined_text = " ".join((str(row.get("d") or ""), effects))
        radii = [float(value) for value in re.findall(r"반경\s*(\d+(?:\.\d+)?)\s*m", combined_text, re.I)]
        skills.append({
            "id": int(row["id"]), "name": name, "source_name": row["n"], "style": style,
            "icon": row.get("i"),
            "ultimate": bool(row.get("u")), "cooldown": nums.get("cd"),
            "motion": (nums.get("m") or 0) / 1000.0, "coefficient": nums.get("total") or 0,
            "targets": targets, "roles": roles, "description": row.get("d", ""),
            "range": known_ranges.get(name, STYLE_RANGES.get(style, 4.0)),
            "radius": max(radii, default=0.0),
            "pull": bool(re.search(r"끌어모|끌어당|흡인", combined_text)),
            "mobility": bool(re.search(r"거리를 단숨에 좁혀|도약|돌진|전진", combined_text)),
            "effects": effects, "effect_details": effect_details,
        })

    builds, page, pages = [], 1, 1
    while page <= pages:
        try:
            data = _get_json(BUILDS_URL.format(page))
        except (OSError, TimeoutError, ValueError):
            builds = previous.get("user_builds", []) if not builds else builds
            break
        pages = min(int(data.get("pages", 1)), 40)
        for build in data.get("items", []):
            def build_name(skill):
                return ALIASES.get((skill.get("style"), skill.get("name")), skill.get("name"))
            builds.append({
                "id": build.get("id"), "name": build.get("name"), "likes": build.get("likes", 0),
                "skills": [build_name(skill) for skill in build.get("slots", [])],
                "order": [build_name(skill) for skill in build.get("order", [])],
                "ultimate": (build.get("ult") or {}).get("name"),
            })
        page += 1
    return {
        "source": CATALOG_URL, "source_date": raw.get("date"),
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "progression_excluded": True,
        "styles": styles,
        "style_icons": {row["n"]: row.get("i") for row in style_rows},
        "style_colors": {row["n"]: row.get("col") for row in style_rows},
        "skills": skills, "user_builds": builds,
    }


def sync_skill_icons(catalog):
    """깨비지지 원본 아이콘을 기존 UI 규격인 64x64 투명 PNG로 보충한다."""
    import cv2
    import numpy as np

    icon_dir = CATALOG_FILE.parent
    index_file = icon_dir / "index.json"
    try:
        index = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    added = 0
    for skill in catalog.get("skills", []):
        name, source = skill["name"], skill.get("icon")
        current = index.get(name)
        if current and (icon_dir / current).is_file():
            continue
        if not source:
            continue
        request = Request(urljoin("https://kkaebigg.com", source),
                          headers={"User-Agent": "GameAutoPlay skill icons/1.0"})
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - 고정 공개 HTTPS 도메인
                encoded = np.frombuffer(response.read(), dtype=np.uint8)
            image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
            if image is None:
                continue
            if image.ndim == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
            elif image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
            height, width = image.shape[:2]
            scale = min(64 / max(1, width), 64 / max(1, height))
            resized = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))),
                                 interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            canvas = np.zeros((64, 64, 4), dtype=np.uint8)
            y = (64 - resized.shape[0]) // 2
            x = (64 - resized.shape[1]) // 2
            canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
            filename = f"web-{int(skill['id']):03d}.png"
            if not cv2.imwrite(str(icon_dir / filename), canvas):
                continue
            index[name] = filename
            added += 1
        except (OSError, ValueError):
            continue
    temporary = index_file.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(index_file)

    style_dir = icon_dir / "styles"
    style_dir.mkdir(parents=True, exist_ok=True)
    style_index_file = style_dir / "index.json"
    try:
        style_index = json.loads(style_index_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        style_index = {}
    for position, (name, source) in enumerate(catalog.get("style_icons", {}).items()):
        current = style_index.get(name)
        if current and (style_dir / current).is_file():
            continue
        if not source:
            continue
        request = Request(urljoin("https://kkaebigg.com", source),
                          headers={"User-Agent": "GameAutoPlay style icons/1.0"})
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - 고정 공개 HTTPS 도메인
                encoded = np.frombuffer(response.read(), dtype=np.uint8)
            image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
            if image is None:
                continue
            if image.ndim == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
            elif image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
            image = cv2.resize(image, (24, 24), interpolation=cv2.INTER_AREA)
            filename = f"style-{position:02d}.png"
            if not cv2.imwrite(str(style_dir / filename), image):
                continue
            style_index[name] = filename
            added += 1
        except (OSError, ValueError):
            continue
    temporary = style_index_file.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(style_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(style_index_file)
    return added


def _casts_60(skill):
    cooldown = float(skill.get("cooldown") or 0)
    coefficient = float(skill.get("coefficient") or 0)
    if cooldown <= 0 or coefficient <= 0:
        return 0.0
    return coefficient * (1 + math.floor((60.0 - 0.001) / cooldown))


def _cast_count_60(skill):
    cooldown = float(skill.get("cooldown") or 0)
    return 0 if cooldown <= 0 else 1 + math.floor((60.0 - 0.001) / cooldown)


STATUS_TOKENS = ("질풍", "기세", "정밀", "원소", "수호", "충격", "관통", "낙인",
                 "파열", "파쇄", "추적", "한기", "출혈", "화상", "감전", "저주",
                 "마비", "빙결", "속박")


@lru_cache(maxsize=512)
def _producer_tokens(text):
    active = set()
    for token in STATUS_TOKENS:
        if (re.search(rf"(?:자신|대상)에게\s+{token}\s+부여", text)
                or re.search(rf"대상을\s+{token}", text)):
            active.add(token)
    return frozenset(active)


def _active_effect_tokens(combo):
    """조합 안에서 실제로 공급되는 자기 버프·대상 상태를 찾는다."""
    active = set()
    for skill in combo:
        text = f"{skill.get('description', '')} {skill.get('effects', '')}"
        active.update(_producer_tokens(text))
    return active


@lru_cache(maxsize=512)
def _condition_tokens(text):
    found = set()
    for token in STATUS_TOKENS:
        if re.search(rf"{token}\s+(?:보유 중|대상 적중 시|상태의 적에게)", text):
            found.add(token)
    return frozenset(found)


@lru_cache(maxsize=4096)
def _effect_damage_cached(text, active_tokens, has_received_damage_debuff):
    """설명에 수치가 명시된 지속/조건부 피해만 보수적으로 1틱 반영한다."""
    required = _condition_tokens(text)
    condition_ok = not required or bool(required & active_tokens)
    extra = 0.0
    if condition_ok:
        extra += sum(float(value) for value in re.findall(
            r"(\d+(?:\.\d+)?)%\s*(?:의\s*)?추가 피해", text))
    # 틱 횟수·지속 시간이 도감에 없어 과대평가하지 않도록 최소 1틱만 더한다.
    extra += sum(float(value) for value in re.findall(
        r"지속적으로\s+(?:공격력의\s*)?(\d+(?:\.\d+)?)%", text))
    base_multiplier = 1.0
    hp_windows = ((r"체력이 25% 이하", 0.25), (r"체력이 75% 이상", 0.25),
                  (r"체력이 50% 이하", 0.50), (r"체력이 50% 이상", 0.50))
    for pattern, uptime in hp_windows:
        if re.search(pattern, text):
            match = re.search(r"주는 피해가\s*(\d+(?:\.\d+)?)%\s*증가", text)
            if match:
                base_multiplier += float(match.group(1)) / 100.0 * uptime
    conditional_increase = re.search(r"(\d+(?:\.\d+)?)%의 피해 증가", text)
    if conditional_increase and condition_ok:
        base_multiplier += float(conditional_increase.group(1)) / 100.0
    received_increase = re.search(r"받는 피해 증가 상태의 적에게.*?(\d+(?:\.\d+)?)% 증가", text)
    if received_increase and has_received_damage_debuff:
        base_multiplier += float(received_increase.group(1)) / 100.0
    return extra, base_multiplier


def _effect_damage_per_cast(skill, active_tokens, has_received_damage_debuff):
    return _effect_damage_cached(str(skill.get("effects") or ""),
                                 frozenset(active_tokens), has_received_damage_debuff)


@lru_cache(maxsize=4096)
def _self_damage_buff_cached(text, cooldown, active_tokens):
    """명시된 공격력/주는 피해 버프를 기본 5초 지속으로 보수 계산한다."""
    if "체력이" in text:
        return 0.0
    required = _condition_tokens(text)
    if required and not (required & active_tokens):
        return 0.0
    values = []
    values += [float(value) for value in re.findall(r"공격력을\s*(\d+(?:\.\d+)?)%\s*증가", text)]
    values += [float(value) for value in re.findall(r"주는 피해(?:가|를)?\s*(\d+(?:\.\d+)?)%\s*증가", text)]
    if not values:
        return 0.0
    durations = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)초", text)]
    duration = max(durations, default=5.0)
    return sum(values) / 100.0 * min(1.0, duration / cooldown)


def _self_damage_buff_uptime(skill, active_tokens):
    return _self_damage_buff_cached(str(skill.get("effects") or ""),
                                    float(skill.get("cooldown") or 60),
                                    frozenset(active_tokens))


@lru_cache(maxsize=512)
def _amp_uptime_cached(text, cooldown):
    values = [float(x) for x in re.findall(r"받는 피해(?:가|를)?\s*(\d+(?:\.\d+)?)%\s*증가", text)]
    if not values:
        return 0.0
    durations = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)초", text)]
    duration = max(durations, default=5.0)
    return max(values) / 100.0 * min(1.0, duration / cooldown)


def _amp_uptime(skill):
    return _amp_uptime_cached(str(skill.get("effects") or ""),
                              float(skill.get("cooldown") or 60))


@lru_cache(maxsize=512)
def _defense_break_cached(text, cooldown):
    """적 방어력 감소를 방어 공식 미공개 상태의 보수값(감소율 50%)으로 환산한다."""
    values = [float(value) for value in re.findall(r"방어력이\s*(\d+(?:\.\d+)?)%\s*감소", text)]
    if not values:
        return 0.0
    durations = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)초", text)]
    duration = max(durations, default=5.0)
    return max(values) / 100.0 * 0.50 * min(1.0, duration / cooldown)


def _defense_break_uptime(skill):
    return _defense_break_cached(str(skill.get("effects") or ""),
                                 float(skill.get("cooldown") or 60))


def effect_indices(catalog, names, ultimate=None):
    """피해와 별도로 강화·약화 설명을 비교 가능한 상대 지수로 계산한다."""
    by_name = {skill["name"]: skill for skill in catalog.get("skills", [])}
    selected = [by_name[name] for name in names if name in by_name]
    if ultimate and ultimate in by_name:
        selected.append(by_name[ultimate])
    enhance, weaken = 0.0, 0.0
    enhance_labels, weaken_labels = set(), set()
    for skill in selected:
        text = str(skill.get("effects") or "")
        cooldown = max(1.0, float(skill.get("cooldown") or 60))
        uptime = min(1.0, 5.0 / cooldown)
        for detail in skill.get("effect_details", []):
            label = str(detail.get("name") or "").strip()
            if not label:
                continue
            if detail.get("type") == "강화":
                enhance_labels.add(label)
            elif detail.get("type") == "약화":
                weaken_labels.add(label)
        for pattern, label, weight in (
            (r"공격력을\s*(\d+(?:\.\d+)?)%\s*증가", "공격력 증가", 1.0),
            (r"주는 피해(?:가|를)?\s*(\d+(?:\.\d+)?)%\s*증가", "주는 피해 증가", 1.0),
            (r"방어력을\s*(\d+(?:\.\d+)?)%\s*증가", "방어력 증가", 0.5),
            (r"받는 피해를\s*(\d+(?:\.\d+)?)%\s*감소", "받는 피해 감소", 1.0),
            (r"치명타 (?:적중|피해)\s*(\d+(?:\.\d+)?)", "치명타 강화", 0.5),
        ):
            values = [float(value) for value in re.findall(pattern, text)]
            if values:
                enhance += sum(values) * weight * uptime
                enhance_labels.add(label)
        if "회복" in text:
            enhance += 3.0 * uptime
            enhance_labels.add("회복")
        if "보호막" in text:
            enhance += 3.0 * uptime
            enhance_labels.add("보호막")
        if "면역" in text or "불사" in text:
            enhance += 2.0 * uptime
            enhance_labels.add("면역/불사")
        for pattern, label, weight in (
            (r"받는 피해(?:가|를)?\s*(\d+(?:\.\d+)?)%\s*증가", "받는 피해 증가", 1.0),
            (r"방어력이\s*(\d+(?:\.\d+)?)%\s*감소", "방어력 감소", 0.5),
            (r"공격력이\s*(\d+(?:\.\d+)?)%\s*감소", "공격력 감소", 0.7),
            (r"이동 ?속도가?\s*(\d+(?:\.\d+)?)%\s*감소", "이동속도 감소", 0.2),
        ):
            values = [float(value) for value in re.findall(pattern, text)]
            if values:
                weaken += sum(values) * weight * uptime
                weaken_labels.add(label)
        for word, label in (("기절", "기절"), ("빙결", "빙결"), ("마비", "마비"),
                            ("속박", "속박"), ("침묵", "침묵"), ("수면", "수면"),
                            ("경직", "경직"), ("변이", "변이")):
            if re.search(rf"(?:대상을\s+{word}|{word}.*행동|{word}을 부여|{word}를 부여)", text):
                weaken += 3.0 * uptime
                weaken_labels.add(label)
        if "보호막을 제거" in text:
            weaken += 2.0 * uptime
            weaken_labels.add("보호막 제거")
        if "받는 회복량" in text or "체력 치유 효과" in text or "시전하는 회복량" in text:
            weaken += 2.0 * uptime
            weaken_labels.add("회복 약화")
    return {"enhance": round(enhance, 2), "weaken": round(weaken, 2),
            "enhance_labels": sorted(enhance_labels), "weaken_labels": sorted(weaken_labels)}


def _ultimate_choice(combo, ultimate_by_style, strict_slots=False):
    """기본 5칸의 3계열 조건으로 활성 궁극기를 고른다."""
    eligible_slots = list(combo[:CORE_SLOTS] if strict_slots else combo)
    family = Counter(skill["style"] for skill in eligible_slots)
    eligible = [style for style, count in family.items()
                if count >= 3 and style in ultimate_by_style]
    main = max(eligible,
               key=lambda style: (family[style], _casts_60(ultimate_by_style[style])),
               default=None)
    return main, ultimate_by_style.get(main)


def _score(combo, mode, ultimate_by_style, strict_slots=False):
    attacks = list(combo)
    main, ultimate = _ultimate_choice(combo, ultimate_by_style, strict_slots)
    if ultimate:
        attacks.append(ultimate)
    total = 0.0
    active_tokens = _active_effect_tokens(combo)
    has_received_damage_debuff = any(_amp_uptime(skill) > 0 for skill in combo)
    for skill in attacks:
        targets = 1 if mode == "single" else max(1, min(int(skill.get("targets", 1)), 10))
        if mode == "aoe":
            targets *= AOE_HIT_FACTORS.get(skill.get("name"), 1.0) * _aoe_reach_factor(skill)
        extra, base_multiplier = _effect_damage_per_cast(
            skill, active_tokens, has_received_damage_debuff)
        total += (_casts_60(skill) * base_multiplier
                  + _cast_count_60(skill) * extra) * targets
    amp = min(0.30, sum(_amp_uptime(skill) + _defense_break_uptime(skill)
                        for skill in attacks))
    self_buff = min(0.25, sum(_self_damage_buff_uptime(skill, active_tokens) for skill in attacks))
    total *= _pack_gather_factor(combo, mode)
    return total * (1.0 + amp) * (1.0 + self_buff) / 60.0, main, ultimate


def _rotation_score(combo, mode, ultimate_by_style, duration=60.0, strict_slots=False):
    """스킬 동작이 겹치지 않는 실제 60초 우선순위 로테이션 DPS를 계산한다."""
    main, ultimate = _ultimate_choice(combo, ultimate_by_style, strict_slots)
    ordered = list(_recommended_order(combo, mode))
    if ultimate:
        ordered.append(ultimate)
    active_tokens = _active_effect_tokens(combo)
    has_received_damage_debuff = any(_amp_uptime(skill) > 0 for skill in combo)
    amp = min(0.30, sum(_amp_uptime(skill) + _defense_break_uptime(skill)
                        for skill in ordered))
    self_buff = min(0.25, sum(_self_damage_buff_uptime(skill, active_tokens)
                              for skill in ordered))
    gather = _pack_gather_factor(combo, mode)
    per_cast = {}
    for skill in ordered:
        targets = 1.0 if mode == "single" else float(max(1, min(int(skill.get("targets", 1)), 10)))
        if mode == "aoe":
            targets *= AOE_HIT_FACTORS.get(skill.get("name"), 1.0) * _aoe_reach_factor(skill)
        extra, base_multiplier = _effect_damage_per_cast(
            skill, active_tokens, has_received_damage_debuff)
        per_cast[skill["name"]] = ((float(skill.get("coefficient") or 0) * base_multiplier + extra)
                                    * targets * gather * (1.0 + amp) * (1.0 + self_buff))
    ready_at = {skill["name"]: 0.0 for skill in ordered}
    casts = Counter()
    total, now = 0.0, 0.0
    while ordered and now < duration - 1e-9:
        skill = next((entry for entry in ordered if ready_at[entry["name"]] <= now + 1e-9), None)
        if skill is None:
            now = min(ready_at.values())
            continue
        name = skill["name"]
        motion = max(0.05, float(skill.get("motion") or 0.05))
        if now + motion > duration + 1e-9:
            break
        total += per_cast[name]
        casts[name] += 1
        ready_at[name] = now + max(0.05, float(skill.get("cooldown") or duration))
        now += motion
    return total / duration, main, ultimate, dict(casts), total


def _recommended_order(combo, mode):
    """상태이상 선행기와 증폭기를 먼저 쓰고 이후 피해기를 배치한다."""
    producer_words = ("파쇄 부여", "한기 부여", "출혈 부여", "화상 부여", "충격 부여", "낙인 부여")
    amplifier_words = ("받는 피해", "방어력이", "방어력 ", "공격력을", "주는 피해")

    def phase(skill):
        text = str(skill.get("effects") or "")
        if mode == "aoe" and skill.get("pull"):
            return -1
        if any(word in text for word in producer_words):
            return 0
        if any(word in text for word in amplifier_words):
            return 1
        return 2

    return sorted(combo, key=lambda skill: (
        phase(skill),
        -int(_amp_uptime(skill) > 0),
        -float(skill.get("range") or 0) if mode == "aoe" else 0,
        -int(skill.get("targets", 1)) if mode == "aoe" else 0,
        float(skill.get("cooldown") or 999),
        -float(skill.get("coefficient") or 0),
    ))


def _slot_order(combo, mode, main):
    """시전 우선순위를 보존하면서 앞 5칸에 메인 계열 3개를 둔다."""
    ordered = list(_recommended_order(combo, mode))
    if not main:
        return ordered
    head, tail = ordered[:CORE_SLOTS], ordered[CORE_SLOTS:]
    need = 3 - sum(skill.get("style") == main for skill in head)
    while need > 0:
        main_index = next((index for index, skill in enumerate(tail)
                           if skill.get("style") == main), None)
        other_index = next((index for index in range(len(head) - 1, -1, -1)
                            if head[index].get("style") != main), None)
        if main_index is None or other_index is None:
            break
        head[other_index], tail[main_index] = tail[main_index], head[other_index]
        need -= 1
    return head + tail


def _growth_priority(catalog, names, mode):
    """모든 도술에 같은 1% 계수 성장을 줬을 때 덱 점수 상승이 큰 순서를 반환한다."""
    by_name = {skill["name"]: skill for skill in catalog.get("skills", [])}
    if any(name not in by_name for name in names):
        return []
    ultimates = {skill["style"]: skill for skill in catalog["skills"] if skill["ultimate"]}
    combo = [by_name[name] for name in names]
    baseline = _rotation_score(combo, mode, ultimates)[0]
    rows = []
    for name in names:
        original = by_name[name]
        if float(original.get("coefficient") or 0) <= 0:
            continue
        grown = dict(original)
        grown["coefficient"] = float(original["coefficient"]) * 1.01
        candidate = [grown if skill["name"] == name else skill for skill in combo]
        score = _rotation_score(candidate, mode, ultimates)[0]
        rows.append({"name": name, "gain": round(score - baseline, 3), "score": round(score, 2)})
    return sorted(rows, key=lambda row: (-row["gain"], row["name"]))


def family_purchase_priority(catalog, simulation):
    """현재 보유 최고 덱에서 계열별 1% 성장 효율과 강화 대상을 계산한다."""
    by_name = {skill["name"]: skill for skill in catalog.get("skills", [])}
    ultimates = {skill["style"]: skill for skill in catalog.get("skills", [])
                 if skill.get("ultimate")}
    styles = list(catalog.get("styles") or [])
    if not styles:
        styles = sorted({skill.get("style") for skill in catalog.get("skills", [])
                         if skill.get("style")})
    result = {style: {"style": style, "aoe_gain": 0.0, "single_gain": 0.0,
                      "aoe_skills": [], "single_skills": [], "ultimates": []}
              for style in styles}
    for mode in ("aoe", "single"):
        rows = simulation.get("current", {}).get(mode, [])
        if not rows:
            continue
        names = rows[0].get("skills", [])
        if any(name not in by_name for name in names):
            continue
        combo = [by_name[name] for name in names]
        baseline = _rotation_score(combo, mode, ultimates)[0]
        for style in styles:
            grown_combo = []
            for skill in combo:
                grown = dict(skill)
                if grown.get("style") == style:
                    grown["coefficient"] = float(grown.get("coefficient") or 0) * 1.01
                grown_combo.append(grown)
            grown_ultimates = {name: dict(skill) for name, skill in ultimates.items()}
            if style in grown_ultimates:
                ultimate = grown_ultimates[style]
                ultimate["coefficient"] = float(ultimate.get("coefficient") or 0) * 1.01
            score = _rotation_score(grown_combo, mode, grown_ultimates)[0]
            gain = ((score / baseline - 1.0) * 100.0) if baseline else 0.0
            result[style][f"{mode}_gain"] = round(max(0.0, gain), 4)
            result[style][f"{mode}_skills"] = [skill["name"] for skill in combo
                                                if skill.get("style") == style]
            main_style = rows[0].get("main_style")
            ultimate = rows[0].get("ultimate")
            if main_style == style and ultimate:
                result[style]["ultimates"].append(str(ultimate))
    for row in result.values():
        row["ultimates"] = list(dict.fromkeys(row["ultimates"]))
        # 자동사냥을 조금 더 중시하되 보스 단일딜도 함께 반영한다.
        row["score"] = round(row["aoe_gain"] * 0.6 + row["single_gain"] * 0.4, 4)
    ranked = sorted(result.values(), key=lambda row: (-row["score"], row["style"]))
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    return ranked


def community_style_summary(catalog, style, owned_names):
    """깨비지지 유저 덱 중 특정 계열 3개 이상인 덱의 인기·성능·미보유를 요약한다."""
    by_name = {skill["name"]: skill for skill in catalog.get("skills", [])}
    ultimates = {skill["style"]: skill for skill in catalog.get("skills", [])
                 if skill.get("ultimate")}
    rows = []
    for build in catalog.get("user_builds", []):
        names = build.get("skills", [])
        if len(names) != SLOTS or any(name not in by_name for name in names):
            continue
        combo = [by_name[name] for name in names]
        if sum(skill.get("style") == style for skill in combo[:CORE_SLOTS]) < 3:
            continue
        rows.append({
            "name": build.get("name") or "이름 없는 덱",
            "likes": int(build.get("likes", 0)),
            "aoe_score": round(_rotation_score(combo, "aoe", ultimates, strict_slots=True)[0], 2),
            "single_score": round(_rotation_score(combo, "single", ultimates, strict_slots=True)[0], 2),
            "missing": [name for name in names if name not in owned_names],
        })
    return sorted(rows, key=lambda row: (row["likes"], row["aoe_score"]), reverse=True)


def _beam_search(skills, owned_names, mode, max_new, min_new=0, beam=16000,
                 required_style=None, min_style=0):
    regular = [skill for skill in skills if not skill["ultimate"] and skill.get("coefficient")]
    ultimates = {skill["style"]: skill for skill in skills if skill["ultimate"] and skill.get("coefficient")}
    states = [(0.0, (), 0)]
    for depth in range(SLOTS):
        candidates = []
        for _old_score, ids, new_count in states:
            start = ids[-1] + 1 if ids else 0
            for idx in range(start, len(regular)):
                missing = regular[idx]["name"] not in owned_names
                next_new = new_count + int(missing)
                if next_new > max_new:
                    continue
                next_ids = ids + (idx,)
                combo = [regular[i] for i in next_ids]
                if required_style:
                    style_count = sum(skill.get("style") == required_style for skill in combo)
                    remaining_slots = SLOTS - len(combo)
                    if style_count + remaining_slots < min_style:
                        continue
                score, _main, _ultimate = _score(combo, mode, ultimates)
                candidates.append((score, next_ids, next_new))
        states = heapq.nlargest(beam, candidates, key=lambda row: row[0])
    valid = [state for state in states if min_new <= state[2] <= max_new]
    if required_style:
        valid = [state for state in valid
                 if sum(regular[index].get("style") == required_style for index in state[1]) >= min_style]
    reranked = []
    for peak, ids, new_count in valid:
        combo = [regular[i] for i in ids]
        exact, main, ultimate, casts, dpm = _rotation_score(combo, mode, ultimates)
        reranked.append((exact, peak, ids, new_count, main, ultimate, casts, dpm))
    results = []
    for exact, peak, ids, new_count, main, ultimate, casts, dpm in heapq.nlargest(
            12, reranked, key=lambda row: row[0]):
        combo = [regular[i] for i in ids]
        ordered = _slot_order(combo, mode, main)
        cast_order = _recommended_order(combo, mode)
        results.append({
            "score": round(exact, 2), "unit": "%공격력/초" if mode == "single" else "%공격력·대상/초",
            "dpm": round(dpm, 2), "peak_score": round(peak, 2), "casts": casts,
            "skills": [skill["name"] for skill in ordered],
            "cast_order": [skill["name"] for skill in cast_order],
            "new_skills": [skill["name"] for skill in combo if skill["name"] not in owned_names],
            "new_count": new_count, "ultimate": ultimate["name"] if ultimate else None,
            "main_style": main,
        })
    return results


def _distinct_top(rows, count=3):
    chosen = []
    for row in rows:
        if any(set(row["skills"]) == set(old["skills"]) for old in chosen):
            continue
        chosen.append(row)
        if len(chosen) == count:
            break
    return chosen


def _f2p_focused_search(catalog, owned_names, mode, style_count, limit=3):
    """보유 도술만으로 2~3개 계열에 투자를 집중한 8칸 조합을 찾는다."""
    owned_names = set(owned_names)
    regular = [skill for skill in catalog.get("skills", [])
               if not skill.get("ultimate") and skill.get("coefficient")
               and skill.get("name") in owned_names]
    ultimates = {skill["style"]: skill for skill in catalog.get("skills", [])
                 if skill.get("ultimate") and skill.get("coefficient")}
    by_style = {}
    for skill in regular:
        by_style.setdefault(skill.get("style"), []).append(skill)
    # 계열별 핵심 후보 5개면 3계열 조합도 충분히 넓게 보면서 계산량을 제한할 수 있다.
    for style, rows in by_style.items():
        by_style[style] = heapq.nlargest(
            5, rows, key=lambda item: _score([item], mode, ultimates)[0])
    viable = sorted(style for style, rows in by_style.items() if len(rows) >= 2)
    winners = []
    for selected in itertools.combinations(viable, style_count):
        pool = [skill for style in selected for skill in by_style[style]]
        if len(pool) < SLOTS:
            continue
        quick = []
        for combo in itertools.combinations(pool, SLOTS):
            counts = Counter(skill.get("style") for skill in combo)
            if any(counts[style] < 2 for style in selected):
                continue
            peak, _main, _ultimate = _score(combo, mode, ultimates)
            if len(quick) < 36:
                heapq.heappush(quick, (peak, tuple(skill["id"] for skill in combo), combo))
            elif peak > quick[0][0]:
                heapq.heapreplace(quick, (peak, tuple(skill["id"] for skill in combo), combo))
        for peak, _ids, combo in quick:
            exact, main, ultimate, casts, dpm = _rotation_score(combo, mode, ultimates)
            ordered = _slot_order(combo, mode, main)
            cast_order = _recommended_order(combo, mode)
            distribution = Counter(skill.get("style") for skill in combo)
            winners.append({
                "score": round(exact, 2),
                "unit": "%공격력/초" if mode == "single" else "%공격력·대상/초",
                "dpm": round(dpm, 2), "peak_score": round(peak, 2), "casts": casts,
                "skills": [skill["name"] for skill in ordered], "new_skills": [], "new_count": 0,
                "cast_order": [skill["name"] for skill in cast_order],
                "ultimate": ultimate["name"] if ultimate else None, "main_style": main,
                "investment_styles": list(selected),
                "style_distribution": {style: distribution[style] for style in selected},
            })
    return _distinct_top(sorted(winners, key=lambda row: row["score"], reverse=True), limit)


def _search_job(args):
    skills, owned_names, mode, max_new, min_new, beam = args
    return mode, max_new, _distinct_top(
        _beam_search(skills, set(owned_names), mode, max_new, min_new, beam), 3)


def _community_results(catalog, owned_names, mode, limit=10):
    by_name = {skill["name"]: skill for skill in catalog["skills"]}
    ultimates = {skill["style"]: skill for skill in catalog["skills"] if skill["ultimate"]}
    rows = []
    for build in catalog.get("user_builds", []):
        names = build.get("skills", [])
        if len(names) != SLOTS or any(name not in by_name for name in names):
            continue
        combo = [by_name[name] for name in names]
        if any(skill["ultimate"] for skill in combo):
            continue
        missing = [skill["name"] for skill in combo if skill["name"] not in owned_names]
        if len(set(missing)) > 4:
            continue
        score, main, ultimate, casts, dpm = _rotation_score(
            combo, mode, ultimates, strict_slots=True)
        peak = _score(combo, mode, ultimates, strict_slots=True)[0]
        order = [name for name in build.get("order", []) if name in names]
        order += [name for name in names if name not in order]
        rows.append({
            "score": round(score, 2), "unit": "%공격력/초" if mode == "single" else "%공격력·대상/초",
            "dpm": round(dpm, 2), "peak_score": round(peak, 2), "casts": casts,
            "skills": names, "new_skills": list(dict.fromkeys(missing)), "new_count": len(set(missing)),
            "cast_order": order,
            "ultimate": ultimate["name"] if ultimate else build.get("ultimate"), "main_style": main,
            "build_name": build.get("name"), "likes": int(build.get("likes", 0)), "source": "깨비지지 유저 덱",
        })
    return sorted(rows, key=lambda row: (row["score"], row["likes"]), reverse=True)[:limit]


def _unlock_priority(catalog, owned_names, mode):
    popularity = Counter()
    for build in catalog.get("user_builds", []):
        weight = math.log2(2 + max(0, int(build.get("likes", 0))))
        for name in set(build.get("skills", [])):
            popularity[name] += weight
    candidates = []
    for skill in catalog["skills"]:
        if skill["ultimate"] or skill["name"] in owned_names or not skill.get("coefficient"):
            continue
        damage, _main, _ultimate, _casts, _dpm = _rotation_score([skill], mode, {})
        value = damage + _amp_uptime(skill) * 250 + popularity[skill["name"]] * 2
        candidates.append((value, skill["name"]))
    return [name for _score_value, name in sorted(candidates, reverse=True)[:4]]


def _heal_swap(catalog, final_row, mode):
    if not final_row:
        return None
    by_name = {skill["name"]: skill for skill in catalog["skills"]}
    healer = by_name.get(HEAL_SKILL)
    names = final_row.get("skills", [])
    if not healer or HEAL_SKILL in names or any(name not in by_name for name in names):
        return None
    ultimates = {skill["style"]: skill for skill in catalog["skills"] if skill["ultimate"]}
    original_combo = [by_name[name] for name in names]
    calculated_original, _original_main, original_ultimate, _casts, _dpm = _rotation_score(
        original_combo, mode, ultimates)
    candidates = []
    for removed in names:
        combo = [by_name[name] for name in names if name != removed] + [healer]
        score, main, ultimate, _casts, _dpm = _rotation_score(combo, mode, ultimates)
        candidates.append((score, removed, combo, main, ultimate))
    score, removed, combo, main, ultimate = max(candidates, key=lambda row: row[0])
    original = float(final_row.get("score") or calculated_original)
    loss = max(0.0, (original - score) / original * 100.0) if original else 0.0
    original_ultimate_name = original_ultimate["name"] if original_ultimate else None
    swapped_ultimate_name = ultimate["name"] if ultimate else None
    keeps_ultimate = original_ultimate_name == swapped_ultimate_name
    reason = "8개 스킬을 하나씩 교체해 본 조합 중 힐 교체 후 예상 데미지가 가장 높아 손실이 가장 적습니다."
    if keeps_ultimate and original_ultimate_name:
        reason += f" 궁극기 {original_ultimate_name}도 유지됩니다."
    elif original_ultimate_name != swapped_ultimate_name:
        reason += (f" 궁극기는 {original_ultimate_name or '미활성'}에서 "
                   f"{swapped_ultimate_name or '미활성'}으로 변경됩니다.")
    return {
        "remove": removed, "add": HEAL_SKILL, "score": round(score, 2),
        "original_score": round(original, 2), "loss_percent": round(loss, 1),
        "skills": [skill["name"] for skill in _slot_order(combo, mode, main)],
        "cast_order": [skill["name"] for skill in _recommended_order(combo, mode)],
        "ultimate": ultimate["name"] if ultimate else None, "main_style": main,
        "original_ultimate": original_ultimate_name, "keeps_ultimate": keeps_ultimate, "reason": reason,
        "condition": "물약 소모가 크거나 체력이 자주 50% 아래로 내려갈 때",
    }


def simulate(catalog, owned_names, beam=16000, parallel=True):
    skills = catalog["skills"]
    output = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_date": catalog.get("source_date"), "catalog_count": len(skills),
        "user_build_count": len(catalog.get("user_builds", [])),
        "owned_count": len(owned_names),
        "modeled_owned_count": len(set(owned_names) & {skill["name"] for skill in skills if not skill["ultimate"]}),
        "unmodeled_owned": sorted(set(owned_names) - {skill["name"] for skill in skills}),
        "slots": SLOTS, "duration_seconds": 60,
        "progression_excluded": True,
        "method": "도술 강화·돌파·각성을 제외한 기본 계수 기준. 앞 5칸의 같은 계열 3개로 궁극기를 활성화하고 8칸 배치와 시전 순서를 분리한다. 60초 로테이션에 대상 수뿐 아니라 사거리·반경·흡인·돌진과 직선형 실적중률을 반영해 몹 유입 후 광역 지속 피해를 계산하고, 상태 연계·조건부 피해·강화/약화와 깨비지지 유저 덱을 함께 재계산한다.",
        "current": {}, "future_3_4": {}, "community": {}, "final": {}, "unlock_priority": {},
        "f2p": {},
        "conditional_heal": {},
    }
    owned_catalog = [skill for skill in skills if skill["name"] in owned_names or skill["ultimate"]]
    jobs = []
    for mode in ("aoe", "single"):
        jobs.append((owned_catalog, tuple(owned_names), mode, 0, 0, beam))
        jobs.append((skills, tuple(owned_names), mode, 4, 0, beam))
    if parallel:
        from concurrent.futures import ProcessPoolExecutor
        workers = max(1, min(len(jobs), max(1, (os.cpu_count() or 2) // 2)))
        with ProcessPoolExecutor(max_workers=workers) as executor:
            rows = list(executor.map(_search_job, jobs))
    else:
        rows = [_search_job(job) for job in jobs]
    for mode, max_new, result in rows:
        output["future_3_4" if max_new else "current"][mode] = result
    for mode in ("aoe", "single"):
        community = _community_results(catalog, owned_names, mode)
        output["community"][mode] = community
        output["unlock_priority"][mode] = _unlock_priority(catalog, owned_names, mode)
        candidates = list(output["future_3_4"][mode]) + community
        output["final"][mode] = max(candidates, key=lambda row: row["score"]) if candidates else None
        output["conditional_heal"][mode] = _heal_swap(catalog, output["final"][mode], mode)
        output["f2p"][mode] = {
            "two_styles": _f2p_focused_search(catalog, owned_names, mode, 2),
            "three_styles": _f2p_focused_search(catalog, owned_names, mode, 3),
        }
    return output


def infer_owned_ultimates(catalog, owned_names):
    """계열별 일반 도술을 3개 이상 보유하면 해당 궁극기를 자동 보유로 판정한다."""
    owned = set(owned_names)
    skills = catalog.get("skills", [])
    ultimates = {skill.get("style"): skill["name"] for skill in skills if skill.get("ultimate")}
    regular_counts = {}
    for skill in skills:
        if not skill.get("ultimate") and skill.get("name") in owned:
            style = skill.get("style")
            regular_counts[style] = regular_counts.get(style, 0) + 1
    owned -= set(ultimates.values())
    owned.update(
        ultimate for style, ultimate in ultimates.items()
        if regular_counts.get(style, 0) >= 3
    )
    return owned


def owned_skill_names():
    import skill_guide
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        selected = saved.get("_owned_skills")
        if isinstance(selected, list) and all(isinstance(name, str) for name in selected):
            catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
            return infer_owned_ultimates(catalog, selected)
    except (OSError, ValueError, TypeError):
        pass
    observed = getattr(skill_guide, "OBSERVED_OWNED_SKILLS", None)
    if observed:
        try:
            catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
            return infer_owned_ultimates(catalog, observed)
        except (OSError, ValueError, KeyError):
            return set(observed)
    names = {row[0] for row in skill_guide.UNLOCKED_SKILLS} | {row[0] for row in skill_guide.NEWLY_LEARNED_SKILLS}
    try:
        catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        names = infer_owned_ultimates(catalog, names)
    except (OSError, ValueError, KeyError):
        pass
    return names


def simulate_saved_catalog(beam=8000):
    """네트워크 갱신 없이 저장된 도감과 사용자가 체크한 보유 도술로 다시 계산한다."""
    catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    results = simulate(catalog, owned_skill_names(), beam=beam)
    RESULT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog, results

def refresh_and_simulate(beam=16000):
    catalog = collect_catalog()
    CATALOG_FILE.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    sync_skill_icons(catalog)
    results = simulate(catalog, owned_skill_names(), beam=beam)
    RESULT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog, results

# End of module.
