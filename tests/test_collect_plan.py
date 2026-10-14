"""화첩 수집 계획: 아이템 사전 조사로 다음 몬스터 고르기 / 완료 기록 / 순서 정렬."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot2 import collect_plan as cp  # noqa: E402


def item(name, sources, monsters=()):
    return {"category": "화첩 조각", "name": name, "sources": sources, "monsters": list(monsters)}


def mon(name, region, power):
    return {"name": name, "region": region, "power": power}


IDICT = {"items": [
    item("해월 화첩 조각", ["콘텐츠"]),
    item("도적 화첩 조각", ["요귀"], [mon("황무지 도적", "황무지 마을", 135000), mon("약한 도적", "안개 기슭", 37000)]),
    item("사슴 화첩 조각", ["요귀"], [mon("연못 흑사슴", "신령의 연못", 5000)]),
    item("호랑이 화첩 조각", ["요귀"], [mon("청산 요수화된 호랑이", "산신의 영역", 68000), mon("청산 세뇌된 호랑이", "산신의 영역", 70000)]),
    item("호랑이 화첩 조각", ["요귀"], [mon("청산 요수화된 호랑이", "산신의 영역", 68000)]),            # 같은 그림이 두 번 잡힌 것
    item("몽련 화첩 조각", ["상자"]),
    {"category": "수집 재료", "name": "작은 가죽", "sources": ["요귀"], "monsters": [mon("가", "나", 1)]},   # 다른 카테고리는 무시
]}


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.ps = cp.paintings_from_dict(IDICT)

    def test_paintings_in_order_and_deduped(self):
        self.assertEqual([p["title"] for p in self.ps], ["해월", "도적", "사슴", "호랑이", "몽련"])
        self.assertEqual(len(self.ps[3]["monsters"]), 2)                      # 중복 중 몬스터가 더 많은 쪽을 씀

    def test_title_of_handles_ocr_variants(self):
        self.assertEqual(cp.title_of("호랑이 화첩 조각"), "호랑이")
        self.assertEqual(cp.title_of("가재 화점 조각"), "가재")
        self.assertEqual(cp.title_of("사승 호법 조각"), "사승")

    def test_first_unfinished_painting_weakest_monster_and_limit(self):
        c = cp.build_candidates(self.ps, {}, power=100000, max_ratio=2.0)
        self.assertEqual([x[3][1]["title"] for x in c], ["도적", "사슴", "호랑이"])        # 순서대로, 요귀 없는 그림은 제외
        self.assertEqual(c[0][2]["name"], "약한 도적")                         # 그림 안에서는 가장 약한 몬스터
        c = cp.build_candidates(self.ps, {}, power=30000, max_ratio=2.0)       # 상한 6만 → 호랑이(68K)는 못 감
        self.assertEqual([x[3][1]["title"] for x in c], ["도적", "사슴"])

    def test_done_banned_and_round_are_skipped(self):
        c = cp.build_candidates(self.ps, {"사슴": 1}, 100000, 2.0, banned={("약한 도적", "안개 기슭")})
        self.assertEqual([x[3][1]["title"] for x in c], ["도적", "호랑이"])
        self.assertEqual(c[0][2]["name"], "황무지 도적")                        # 약한 도적은 제외돼 다음 몬스터
        self.assertEqual(cp.build_candidates(self.ps, {}, 0), [])             # 전투력을 모르면 후보 없음


class AlignTest(unittest.TestCase):
    def test_done_paintings_matched_by_order_even_with_garbled_names(self):
        ps = [{"title": t, "item": t, "order": i, "sources": ["요귀"], "monsters": []}
              for i, t in enumerate(["해월", "선인", "자습", "공", "호랑이", "백로"])]          # '자습'=사슴을 잘못 읽음
        album = [{"name": "해월", "done": False}, {"name": "선인", "done": False}, {"name": "사슴", "done": True},
                 {"name": "?", "done": False}, {"name": "호랑이", "done": False}, {"name": "백로", "done": True}]
        done = {}
        self.assertEqual(cp.sync_done(done, ps, album, now=5.0), 2)
        self.assertEqual(set(done), {"자습", "백로"})
        self.assertEqual(cp.sync_done(done, ps, album), 0)                    # 이미 기록한 것은 다시 더하지 않음

    def test_done_file_roundtrip(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "done.json")
        cp.save_done({"호랑이": 1.0}, path)
        self.assertEqual(cp.load_done(path), {"호랑이": 1.0})
        self.assertEqual(cp.load_done(path + ".none"), {})


if __name__ == "__main__":
    unittest.main()
