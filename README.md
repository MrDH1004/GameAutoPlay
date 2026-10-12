# GameAutoPlay — 도깨비의세계 퀘스트 자동 진행 보조

자동 진행이 멈추면 오른쪽 퀘스트 목록을 클릭하고, 대화/보상/수락 팝업을 눌러 주는 화면 인식 매크로.
게임 메모리·패킷은 건드리지 않고 화면 캡처 + 마우스/키보드 입력만 사용한다.

> ⚠️ 게임 폴더에 안티치트 로더(`ucldr_DKBI_GL_loader_x64.exe`)가 있다. 입력 매크로는 운영 정책 위반으로
> 계정 제재를 받을 수 있으니 사용 전 게임 약관을 확인할 것.

## 설치
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 사용 순서
게임이 관리자 권한으로 실행되므로 **아래 명령은 모두 관리자 PowerShell에서** 실행해야 클릭이 전달된다.

1. **스크린샷 수집** — 게임 중 멈추는 화면마다 F9 (F12 종료)
   ```powershell
   .\.venv\Scripts\python.exe -m tools.snap
   ```
2. **템플릿 자르기** — 버튼을 드래그해 `templates/`에 저장, 출력된 비율 좌표는 `config.yaml`에 기입
   ```powershell
   .\.venv\Scripts\python.exe -m tools.crop screenshots\파일이름.png
   ```
   | 파일 | 내용 |
   |---|---|
   | `quest_first.png` | 오른쪽 퀘스트 목록 첫 항목 (또는 `quest_tracker.click` 좌표만 설정) |
   | `auto_running.png` | "자동 진행 중" 표시 (있으면 멈춤 감지가 정확해짐) |
   | `dialog_skip.png`, `dialog_next.png` | 대화 스킵/다음 |
   | `reward.png`, `accept.png` | 완료·보상 / 수락 |
   | `cutscene_skip.png`, `revive.png` | 컷신 / 부활 |
3. **dry-run 확인** — 클릭 없이 감지 로그만 출력
   ```powershell
   .\.venv\Scripts\python.exe main.py --dry-run --debug
   ```
4. **실행**
   ```powershell
   .\.venv\Scripts\python.exe main.py
   ```

## 영기 수련 자동 진행
`config.yaml`의 `yeonggi.interval`(기본 180초)마다 필드 화면에서:
1. ☰ 메뉴 → `영기 수련` 열기
2. 하단 모래시계 바가 있으면(진행 중) 바로 복귀
3. 없으면 `0/1` 노드를 왼쪽부터 클릭 → "현재 수련이 불가능합니다"면 닫고 다음 노드 → `수련` 클릭
4. 뒤로가기로 필드 복귀

영기 수련만 돌리려면 `quest_tracker.enabled: false`, 끄려면 `yeonggi.enabled: false`.
dry-run에서는 메뉴를 열 수 없어 영기 수련 단계는 로그만 남긴다.

## 단축키 / 안전장치
- `F12` 종료, `F11` 일시정지·재개
- 마우스를 화면 왼쪽 위 모서리로 옮기면 비상 정지
- 게임 창이 맨 앞에 있을 때만 동작
- 퀘스트를 `max_retries`번 눌러도 진행이 없으면 화면 저장(`screenshots/stuck_*.png`) + 경고음 + 일시정지

## 구조
```
main.py            진입점
config.yaml        설정 (창 제목, 좌표, 템플릿, 팝업 우선순위)
bot/window.py      게임 창 찾기·캡처
bot/vision.py      템플릿 매칭, 화면 변화(멈춤) 감지
bot/actions.py     클릭·키 입력, 단축키
bot/runner.py      메인 루프 (팝업 → 멈춤 감지 → 퀘스트 클릭)
tools/snap.py      스크린샷 수집
tools/crop.py      템플릿 자르기
```
