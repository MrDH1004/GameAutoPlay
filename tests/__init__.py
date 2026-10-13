"""테스트 공통 설정: 테스트가 만드는 제어판 창이 실제 로그 파일(logs/bot2.log)에 쓰지 않게 한다."""
import os

os.environ.setdefault("DOKKAEBI_TEST", "1")
