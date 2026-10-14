"""아이템 사전 스캔의 화면 없는 부분: 페이지 이어 붙이기 / 전투력 글자 읽기 / 이름 통일 / 전투력 채우기."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot2 import itemdict as idict  # noqa: E402


class Fake:
    power_cache = {}
    known_powers = idict.ItemDictScanner.known_powers
    _snap_power = idict.ItemDictScanner._snap_power


class MergeTest(unittest.TestCase):
    def test_overlap_is_not_repeated(self):
        seen = ["가", "나", "다", "라"]
        n = idict.ItemDictScanner.merge_pages(seen, ["다", "라", "마", "바"])
        self.assertEqual(n, 2)
        self.assertEqual(seen, ["가", "나", "다", "라", "마", "바"])

    def test_same_name_twice_in_a_row_is_kept(self):
        seen = []
        idict.ItemDictScanner.merge_pages(seen, ["작은 가죽", "작은 가죽", "작은 모피"])
        self.assertEqual(len(seen), 3)

    def test_end_of_list_adds_nothing(self):
        seen = ["가", "나", "다"]
        self.assertEqual(idict.ItemDictScanner.merge_pages(seen, ["가", "나", "다"]), 0)


class PowerTest(unittest.TestCase):
    def setUp(self):
        self.f = Fake()
        self.f.power_cache = {"a": 94500, "b": 110000}

    def test_noisy_badges_snap_to_known_values(self):
        self.assertEqual(self.f._snap_power("Y945K"), 94500)           # 소수점이 빠진 경우
        self.assertEqual(self.f._snap_power("x94.5K"), 94500)
        self.assertEqual(self.f._snap_power("Y94:5K"), 94500)
        self.assertEqual(self.f._snap_power("x110K"), 110000)

    def test_unknown_or_garbage_is_none(self):
        self.assertIsNone(self.f._snap_power("hello"))
        self.assertIsNone(self.f._snap_power("x77K"))                  # 아는 값 중에 없음 → 지어내지 않는다

    def test_parse_power(self):
        self.assertEqual(idict.parse_power("⚔15.5K"), 15500)
        self.assertEqual(idict.parse_power("2M"), 2_000_000)


def mon(name, region, power):
    return {"name": name, "region": region, "power": power}


class CleanupTest(unittest.TestCase):
    def test_names_unified_and_powers_filled(self):
        db = {"items": [
            {"monsters": [mon("청산 지하 새앙이리", "산신의 영역 지하 동굴", 94500), mon("청산 지하 요기말벌", "산신의 영역 지하 동굴", 94500)]},
            {"monsters": [mon("청산 지하 새앙이리", "산신의 영역 지하 동굴", 94500)]},
            {"monsters": [mon("청산 지하 새암이리", "산신의 영역 지하 동굴", None)]},              # 철자 흔들림 + 못 읽음
            {"monsters": [mon("청산 지하 요기말벌", "산신의 영역 지하 동굴", 6000)]},             # 오독(6K)
            {"monsters": [mon("청산 지하 암흑 스라소니", "산신의 영역 지하 동굴", 94500)] * 3},
        ]}
        c, f = idict.finalize(db)
        self.assertGreaterEqual(c, 1)
        names = {m["name"] for it in db["items"] for m in it["monsters"]}
        self.assertNotIn("청산 지하 새암이리", names)
        for it in db["items"]:
            for m in it["monsters"]:
                self.assertEqual(m["power"], 94500, m)
        self.assertGreaterEqual(f, 2)

class RegionAndDigitTest(unittest.TestCase):
    def test_region_noise_and_spelling_fixed(self):
        db = {"items": [{"monsters": [mon("가", "안개 기습", 37000), mon("나", "9)) 안개 기슭", 37000), mon("다", "우물동굴", 170000),
                                      mon("라", "구 안개 기슭", 38500)]}]}
        idict.canonicalize_regions(db)
        self.assertEqual([m["region"] for m in db["items"][0]["monsters"]], ["안개 기슭", "안개 기슭", "우물 동굴", "안개 기슭"])

    def test_dropped_or_extra_digit_fixed_from_region_values(self):
        ms = [mon("a", "황무지 마을", 135000)] * 3 + [mon("b", "황무지 마을", 35000), mon("c", "안개 기슭", 38500),
                                                       mon("d", "안개 기슭", 385000), mon("e", "안개 기슭", 37000)]
        db = {"items": [{"monsters": ms}]}
        n = idict.fix_power_digits(db)
        self.assertEqual(n, 2)
        self.assertEqual(db["items"][0]["monsters"][3]["power"], 135000)       # 135K에서 첫 자리 빠짐
        self.assertEqual(db["items"][0]["monsters"][5]["power"], 38500)        # 38.5K가 385K로


if __name__ == "__main__":
    unittest.main()
