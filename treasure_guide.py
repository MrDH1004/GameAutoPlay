"""보물찾기 안내 데이터와 수동 진행률 계산.

지도 좌표는 비공식 팬 공략을 바탕으로 하며 헬퍼는 게임에 입력을 보내지 않는다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TreasureRegion:
    key: str
    province: str
    name: str
    power: int
    normal: int
    rare: int
    legendary: int
    image: str

    @property
    def count(self):
        return self.normal + self.rare + self.legendary


REGIONS = (
    TreasureRegion("tabaro_castle", "타바로 지방", "타바로 성", 0, 6, 6, 0, "3-treasure-map-tabaro-castle-1.png"),
    TreasureRegion("sinryeong_pond", "타바로 지방 · 영험한 청산", "신령의 연못", 8_000, 1, 3, 0, "4-treasure-map-sinryeong-pond-1.png"),
    TreasureRegion("rock_valley", "타바로 지방 · 영험한 청산", "암석 계곡", 13_000, 1, 3, 0, "5-treasure-map-rock-valley-1.png"),
    TreasureRegion("dry_swamp", "타바로 지방 · 영험한 청산", "메마른 늪", 15_000, 1, 4, 0, "6-treasure-map-dry-swamp-1.png"),
    TreasureRegion("fog_foothill", "타바로 지방 · 영험한 청산", "안개 기슭", 33_300, 1, 3, 0, "7-treasure-map-fog-foothill-1.png"),
    TreasureRegion("fog_cave", "타바로 지방 · 영험한 청산", "안개 기슭 지하 동굴", 45_900, 1, 3, 0, "8-treasure-map-fog-cave-1.png"),
    TreasureRegion("sanshin_zone", "타바로 지방 · 영험한 청산", "산신의 영역", 61_200, 1, 3, 0, "9-treasure-map-sanshin-zone-1.png"),
    TreasureRegion("sanshin_cave", "타바로 지방 · 영험한 청산", "산신의 영역 지하 동굴", 85_050, 1, 3, 0, "10-treasure-map-sanshin-cave-1.png"),
    TreasureRegion("gloomy_mine", "수아리 지방 · 비명 동굴", "음산한 폐광", 234_000, 1, 3, 1, "11a-treasure-map-pyegwang-1.png"),
    TreasureRegion("poison_zone", "수아리 지방 · 비명 동굴", "독기 지대", 279_000, 2, 3, 1, "11b-treasure-map-dokgi-1.png"),
    TreasureRegion("yogi_cave", "수아리 지방 · 비명 동굴", "요기의 동굴", 324_000, 1, 2, 1, "12-treasure-map-yogi-cave-1.png"),
    TreasureRegion("plague_hatchery", "수아리 지방 · 비명 동굴", "역병의 부화장", 346_500, 1, 2, 1, "13-treasure-map-plague-hatchery-1.png"),
    TreasureRegion("sorrow_echo", "수아리 지방 · 비명 동굴", "비탄의 울림", 400_500, 1, 2, 1, "14-treasure-map-bitan-1.png"),
    TreasureRegion("village_entrance", "가모리 지방 · 귀명촌", "마을 어귀", 441_000, 1, 3, 1, "15-treasure-map-village-entrance-1.png"),
    TreasureRegion("village_ruins", "가모리 지방 · 귀명촌", "마을 폐허", 508_500, 2, 3, 1, "16-treasure-map-village-ruins-1.png"),
    TreasureRegion("skull_cave", "가모리 지방 · 귀명촌", "숨겨진 해골 동굴", 549_000, 1, 2, 1, "17-treasure-map-skull-cave-1.png"),
    TreasureRegion("donggubak", "가모리 지방 · 귀명촌", "동구밖", 625_500, 1, 2, 1, "18-treasure-map-donggubak-1.png"),
    TreasureRegion("spell_cave", "가모리 지방 · 귀명촌", "주술 결계 동굴", 738_000, 1, 2, 1, "19-treasure-map-jusul-cave-1.png"),
    TreasureRegion("gogaetmaru", "가모리 지방 · 귀명촌", "고갯마루", 864_000, 1, 2, 1, "20-treasure-map-gogaetmaru-1.png"),
)

TOTAL_CHESTS = sum(region.count for region in REGIONS)
SOURCE_PAGE = "https://gcouponplay.com/dokkaebi-world-treasure-chest-location/"
INTERACTIVE_MAP = "https://kkaebi.gg/treasure"


def normalize_progress(data):
    """저장 파일의 범위 밖 번호와 형식 오류를 제거한다."""
    if not isinstance(data, dict):
        return {}
    valid = {region.key: region.count for region in REGIONS}
    cleaned = {}
    for key, values in data.items():
        if key not in valid or not isinstance(values, list):
            continue
        found = sorted({value for value in values if isinstance(value, int) and not isinstance(value, bool)
                        and 1 <= value <= valid[key]})
        if found:
            cleaned[key] = found
    return cleaned


def progress_summary(progress):
    progress = normalize_progress(progress)
    found = sum(len(values) for values in progress.values())
    completed = sum(1 for region in REGIONS if len(progress.get(region.key, ())) == region.count)
    return found, TOTAL_CHESTS, completed, len(REGIONS)


def recommended_region(power, progress=None):
    """현재 전투력으로 조사할 수 있는 첫 미완료 지역. 전투력이 없으면 첫 미완료 지역."""
    progress = normalize_progress(progress or {})
    candidates = [region for region in REGIONS
                  if len(progress.get(region.key, ())) < region.count and (power <= 0 or region.power <= power)]
    return candidates[0] if candidates else None
