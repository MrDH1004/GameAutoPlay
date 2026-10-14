@echo off
chcp 65001 >nul
rem 제어판 실행 (관리자 권한은 gui.py가 알아서 요청한다)
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" gui.py --autostart
  exit /b 0
)
if exist "dist\GameAutoPlay\GameAutoPlay.exe" (
  start "" "dist\GameAutoPlay\GameAutoPlay.exe" --autostart
  exit /b 0
)
echo 실행 파일과 Python 환경을 찾지 못했습니다. README의 설치 방법을 확인하세요.
pause
exit /b 1
