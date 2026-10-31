import unittest

from skill_simulator import (community_style_summary, effect_indices, family_purchase_priority,
                             infer_owned_ultimates, _aoe_reach_factor, _growth_priority, _rotation_score, _score,
                             _slot_order, range_profile, simulate)


def skill(idx, name, style, coefficient, cooldown, targets=1, ultimate=False, effects=""):
    return {"id": idx, "name": name, "style": style, "coefficient": coefficient,
            "cooldown": cooldown, "targets": targets, "ultimate": ultimate, "effects": effects}


class SkillSimulatorTest(unittest.TestCase):
    def test_three_regular_skills_automatically_own_the_style_ultimate(self):
        catalog = {"skills": [
            skill(1, "일반1", "갑", 10, 5),
            skill(2, "일반2", "갑", 10, 5),
            skill(3, "일반3", "갑", 10, 5),
            skill(4, "궁극", "갑", 100, 20, ultimate=True),
        ]}
        self.assertNotIn("궁극", infer_owned_ultimates(catalog, {"일반1", "일반2"}))
        self.assertIn("궁극", infer_owned_ultimates(catalog, {"일반1", "일반2", "일반3"}))
        self.assertNotIn("궁극", infer_owned_ultimates(catalog, {"일반1", "궁극"}))

    def test_aoe_values_targets_but_single_does_not(self):
        one = skill(1, "단일", "갑", 100, 10, 1)
        wide = skill(2, "광역", "갑", 80, 10, 5)
        self.assertGreater(_score([wide], "aoe", {})[0], _score([one], "aoe", {})[0])
        self.assertGreater(_score([one], "single", {})[0], _score([wide], "single", {})[0])

    def test_straight_line_skill_gets_real_world_aoe_hit_penalty(self):
        straight = skill(1, "대검 강타", "강타", 100, 10, 6)
        circle = skill(2, "원형 광역", "강타", 100, 10, 6)
        self.assertLess(_score([straight], "aoe", {})[0], _score([circle], "aoe", {})[0])
        self.assertEqual(_score([straight], "single", {})[0], _score([circle], "single", {})[0])

    def test_forward_arc_skill_gets_aoe_hit_penalty_only(self):
        forward = skill(1, "검기 날리기", "강타", 100, 10, 5)
        radial = skill(2, "영혼 소용돌이", "주술", 100, 10, 5)
        self.assertLess(_score([forward], "aoe", {})[0], _score([radial], "aoe", {})[0])
        self.assertEqual(_score([forward], "single", {})[0], _score([radial], "single", {})[0])

    def test_long_range_and_pull_raise_aoe_value_only(self):
        short = skill(1, "근접 광역", "강타", 100, 10, 5)
        long = skill(2, "원거리 광역", "파멸", 100, 10, 5)
        pull = skill(3, "흡인 광역", "파멸", 100, 10, 5)
        short["range"], long["range"], pull["range"] = 2.0, 6.0, 6.0
        pull["pull"] = True
        self.assertGreater(_score([long], "aoe", {})[0], _score([short], "aoe", {})[0])
        follow = skill(4, "후속 광역", "파멸", 500, 10, 5)
        follow["range"] = 6.0
        self.assertGreater(_score([pull, follow], "aoe", {})[0],
                           _score([long, follow], "aoe", {})[0])
        self.assertEqual(_score([long], "single", {})[0], _score([short], "single", {})[0])

    def test_range_profile_reports_pack_coverage(self):
        short = skill(1, "근접 광역", "강타", 100, 10, 5)
        pull = skill(2, "흡인 광역", "파멸", 100, 10, 5)
        short["range"], pull["range"], pull["pull"] = 2.0, 6.0, True
        profile = range_profile({"skills": [short, pull]}, ["근접 광역", "흡인 광역"])
        self.assertEqual(profile["average"], 4.0)
        self.assertEqual(profile["long_aoe"], 1)
        self.assertEqual(profile["pull_skills"], ["흡인 광역"])
        self.assertGreater(_aoe_reach_factor(pull), _aoe_reach_factor(short))

    def test_description_condition_requires_a_matching_status_producer(self):
        finisher = skill(1, "연계기", "갑", 100, 10, effects="충격 대상 적중 시 20% 추가 피해")
        producer = skill(2, "충격기", "갑", 1, 60, effects="타격 성공 시 대상에게 충격 부여")
        plain = skill(3, "일반기", "갑", 1, 60)
        without = _score([finisher, plain], "single", {})[0]
        with_status = _score([finisher, producer], "single", {})[0]
        self.assertGreater(with_status, without)

    def test_attack_buff_in_description_increases_combo_damage(self):
        damage = skill(1, "피해기", "갑", 100, 10)
        buff = skill(2, "버프기", "갑", 1, 10, effects="공격력을 10% 증가시킵니다.")
        plain = skill(3, "일반기", "갑", 1, 10)
        self.assertGreater(_score([damage, buff], "single", {})[0],
                           _score([damage, plain], "single", {})[0])

    def test_dot_description_adds_conservative_damage_tick(self):
        dot = skill(1, "지속기", "갑", 100, 10, effects="대상에게 지속적으로 공격력의 5%만큼 피해를 입힙니다.")
        plain = skill(2, "일반기", "갑", 100, 10)
        self.assertGreater(_score([dot], "single", {})[0], _score([plain], "single", {})[0])

    def test_defense_break_increases_damage_and_effect_indices_are_separate(self):
        damage = skill(1, "피해기", "갑", 100, 10)
        breaker = skill(2, "약화기", "갑", 1, 10, effects="방어력이 10% 감소합니다. 대상을 기절시킵니다.")
        plain = skill(3, "일반기", "갑", 1, 10)
        self.assertGreater(_score([damage, breaker], "single", {})[0],
                           _score([damage, plain], "single", {})[0])
        catalog = {"skills": [damage, breaker]}
        indices = effect_indices(catalog, ["피해기", "약화기"])
        self.assertGreater(indices["weaken"], 0)
        self.assertIn("방어력 감소", indices["weaken_labels"])

    def test_enhance_index_detects_heal_shield_and_damage_reduction(self):
        support = skill(1, "지원기", "헌신", 0, 20,
                        effects="최대 1회 회복합니다. 보호막을 부여합니다. 받는 피해를 5% 감소시킵니다.")
        indices = effect_indices({"skills": [support]}, ["지원기"])
        self.assertGreater(indices["enhance"], 0)
        self.assertIn("회복", indices["enhance_labels"])
        self.assertIn("보호막", indices["enhance_labels"])

    def test_official_enhance_and_weaken_names_are_kept(self):
        official = skill(1, "공식효과", "갑", 1, 10)
        official["effect_details"] = [
            {"type": "강화", "name": "공격 강화", "lines": []},
            {"type": "약화", "name": "방어 약화", "lines": []},
        ]
        indices = effect_indices({"skills": [official]}, ["공식효과"])
        self.assertIn("공격 강화", indices["enhance_labels"])
        self.assertIn("방어 약화", indices["weaken_labels"])

    def test_three_same_style_enables_ultimate(self):
        normal = [skill(i, f"기술{i}", "갑", 100, 10) for i in range(3)]
        ultimate = skill(99, "궁극", "갑", 1000, 30, ultimate=True)
        with_ultimate = _score(normal, "single", {"갑": ultimate})
        without = _score(normal, "single", {})
        self.assertEqual(with_ultimate[1], "갑")
        self.assertGreater(with_ultimate[0], without[0])

    def test_strict_slots_only_use_first_five_for_ultimate(self):
        other = [skill(i, f"타계열{i}", "을", 10, 10) for i in range(5)]
        main = [skill(10 + i, f"주계열{i}", "갑", 100, 10) for i in range(3)]
        ultimate = skill(99, "궁극", "갑", 1000, 30, ultimate=True)
        flexible = _rotation_score(other + main, "single", {"갑": ultimate})
        strict = _rotation_score(other + main, "single", {"갑": ultimate}, strict_slots=True)
        self.assertEqual(flexible[1], "갑")
        self.assertIsNone(strict[1])

    def test_slot_order_puts_three_main_style_skills_in_first_five(self):
        combo = [skill(i, f"타계열{i}", "을", 200 - i, 5) for i in range(5)]
        combo += [skill(10 + i, f"주계열{i}", "갑", 100 - i, 20) for i in range(3)]
        arranged = _slot_order(combo, "single", "갑")
        self.assertEqual(sum(row["style"] == "갑" for row in arranged[:5]), 3)

    def test_future_result_uses_three_or_four_new_skills(self):
        skills = [skill(i, f"기술{i}", "갑" if i < 6 else "을", 100 + i, 5 + i % 3,
                        5 if i % 2 else 1) for i in range(12)]
        skills += [skill(90, "갑궁", "갑", 500, 30, ultimate=True),
                   skill(91, "을궁", "을", 500, 30, ultimate=True)]
        catalog = {"skills": skills, "user_builds": [], "source_date": "test"}
        result = simulate(catalog, {f"기술{i}" for i in range(6)}, beam=1000, parallel=False)
        for mode in ("aoe", "single"):
            self.assertTrue(result["future_3_4"][mode])
            self.assertTrue(all(0 <= row["new_count"] <= 4 for row in result["future_3_4"][mode]))
            self.assertEqual(len(result["unlock_priority"][mode]), 4)
            self.assertIn("dpm", result["future_3_4"][mode][0])
            self.assertAlmostEqual(result["future_3_4"][mode][0]["dpm"],
                                   result["future_3_4"][mode][0]["score"] * 60, delta=1.0)

    def test_f2p_results_use_owned_skills_and_only_two_or_three_families(self):
        skills = []
        for style_index, style_name in enumerate(("갑", "을", "병")):
            skills += [skill(style_index * 10 + i, f"{style_name}{i}", style_name,
                             100 + i * 5, 6 + i % 2, targets=5) for i in range(5)]
            skills.append(skill(90 + style_index, f"{style_name}궁", style_name,
                                500, 30, targets=5, ultimate=True))
        owned = {row["name"] for row in skills if not row["ultimate"]}
        catalog = {"skills": skills, "user_builds": [], "source_date": "test"}
        result = simulate(catalog, owned, beam=300, parallel=False)
        for mode in ("aoe", "single"):
            for key, expected_styles in (("two_styles", 2), ("three_styles", 3)):
                row = result["f2p"][mode][key][0]
                self.assertEqual(len(row["investment_styles"]), expected_styles)
                self.assertEqual(len(row["skills"]), 8)
                self.assertFalse(row["new_skills"])
                self.assertTrue(set(row["skills"]).issubset(owned))
                self.assertTrue(all(count >= 2 for count in row["style_distribution"].values()))

    def test_rotation_does_not_overlap_long_skill_motions(self):
        from skill_simulator import _rotation_score
        first = skill(1, "긴동작1", "갑", 100, 1)
        second = skill(2, "긴동작2", "을", 100, 1)
        first["motion"] = second["motion"] = 10.0
        dps, _main, _ultimate, casts, dpm = _rotation_score([first, second], "single", {})
        self.assertEqual(sum(casts.values()), 6)
        self.assertAlmostEqual(dpm, 600.0)
        self.assertAlmostEqual(dps, 10.0)

    def test_conditional_heal_replaces_one_skill(self):
        skills = [skill(i, f"기술{i}", "갑", 100 + i, 6) for i in range(8)]
        skills += [skill(80, "생명의 축복", "헌신", 0, 14, targets=5),
                   skill(90, "갑궁", "갑", 500, 30, ultimate=True)]
        catalog = {"skills": skills, "user_builds": [], "source_date": "test"}
        result = simulate(catalog, {row["name"] for row in skills}, beam=1000, parallel=False)
        for mode in ("aoe", "single"):
            swap = result["conditional_heal"][mode]
            self.assertEqual(swap["add"], "생명의 축복")
            self.assertEqual(len(swap["skills"]), 8)
            self.assertIn("생명의 축복", swap["skills"])
            self.assertGreater(swap["original_score"], swap["score"])
            self.assertGreater(swap["loss_percent"], 0)
            self.assertIn("손실이 가장 적습니다", swap["reason"])

    def test_growth_priority_puts_larger_damage_gain_first(self):
        low = skill(1, "낮은딜", "갑", 100, 10)
        high = skill(2, "높은딜", "갑", 300, 10)
        catalog = {"skills": [low, high]}
        rows = _growth_priority(catalog, ["낮은딜", "높은딜"], "single")
        self.assertEqual(rows[0]["name"], "높은딜")
        self.assertGreater(rows[0]["gain"], rows[1]["gain"])

    def test_family_purchase_priority_explains_affected_skills(self):
        skills = [skill(1, "강타기", "강타", 300, 10),
                  skill(2, "연타기", "연타", 100, 10),
                  skill(90, "강타궁", "강타", 500, 30, ultimate=True)]
        catalog = {"styles": ["강타", "연타", "기동"], "skills": skills}
        simulation = {"current": {"aoe": [{"skills": ["강타기", "연타기"],
                                               "main_style": None, "ultimate": None}],
                                  "single": [{"skills": ["강타기", "연타기"],
                                                  "main_style": None, "ultimate": None}]}}
        rows = family_purchase_priority(catalog, simulation)
        self.assertEqual(rows[0]["style"], "강타")
        self.assertIn("강타기", rows[0]["single_skills"])
        self.assertEqual(rows[-1]["style"], "기동")

    def test_community_style_summary_reports_missing_skills(self):
        skills = [skill(i, f"기동{i}", "기동", 100, 10) for i in range(8)]
        catalog = {"skills": skills, "user_builds": [{"name": "기동덱", "likes": 7,
                                                         "skills": [x["name"] for x in skills]}]}
        rows = community_style_summary(catalog, "기동", {"기동0"})
        self.assertEqual(rows[0]["name"], "기동덱")
        self.assertEqual(len(rows[0]["missing"]), 7)

    def test_catalog_snapshot_contains_all_public_skills(self):
        import json
        from skill_simulator import CATALOG_FILE, RESULT_FILE, owned_skill_names
        catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        result = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(catalog["skills"]), 105)
        self.assertGreaterEqual(len(catalog["user_builds"]), 70)
        self.assertEqual(result["owned_count"], len(owned_skill_names()))

    def test_every_catalog_skill_has_local_64px_icon(self):
        import json
        import struct
        from skill_simulator import CATALOG_FILE
        catalog = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        index_file = CATALOG_FILE.parent / "index.json"
        index = json.loads(index_file.read_text(encoding="utf-8"))
        for skill in catalog["skills"]:
            self.assertIn(skill["name"], index)
            with (CATALOG_FILE.parent / index[skill["name"]]).open("rb") as image:
                header = image.read(26)
            self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
            width, height, _depth, color_type = struct.unpack(">IIBB", header[16:26])
            self.assertEqual((width, height, color_type), (64, 64, 6), skill["name"])


if __name__ == "__main__":
    unittest.main()
