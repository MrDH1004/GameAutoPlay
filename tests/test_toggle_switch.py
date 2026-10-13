"""토글 스위치: QCheckBox 호환(값 읽기/쓰기·시그널)과 애니메이션 진행값."""
import os

os.environ.setdefault("DOKKAEBI_TEST", "1")      # 테스트가 실제 로그 파일에 쓰지 않게
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox  # noqa: E402

from toggle_switch import Toggle  # noqa: E402

app = QApplication.instance() or QApplication([])


def pump(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class ToggleTest(unittest.TestCase):
    def tearDown(self):
        for w in app.topLevelWidgets():                      # 위젯을 정리해 종료 시 경고가 안 남게
            w.close()
            w.deleteLater()
        pump(10)

    def test_is_a_checkbox_and_keeps_value_api(self):
        t = Toggle("테스트")
        self.assertIsInstance(t, QCheckBox)                  # 설정 저장/불러오기 코드가 isinstance(QCheckBox)로 구분함
        self.assertFalse(t.isChecked())
        t.setChecked(True)
        self.assertTrue(t.isChecked())
        self.assertEqual(t.text(), "테스트")

    def test_set_checked_jumps_without_animation(self):
        t = Toggle("a")
        t.setChecked(True)
        self.assertEqual(t.progress, 1.0)                    # 프로필 불러오기는 즉시 그 상태
        t.setChecked(False)
        self.assertEqual(t.progress, 0.0)

    def test_click_animates_through_intermediate_values(self):
        t = Toggle("a")
        t.show()
        seen = []
        t.click()
        for _ in range(8):
            pump(30)
            seen.append(t.progress)
        self.assertTrue(t.isChecked())
        self.assertTrue(any(0.05 < v < 0.95 for v in seen), seen)   # 중간 단계를 거침
        self.assertEqual(seen[-1], 1.0)                               # 끝에는 켜짐 위치
        self.assertEqual(seen, sorted(seen))                          # 되돌아가지 않고 단조 증가

    def test_toggled_signal_still_emitted(self):
        t = Toggle("a")
        got = []
        t.toggled.connect(got.append)
        t.click()
        t.click()
        self.assertEqual(got, [True, False])


if __name__ == "__main__":
    unittest.main()
