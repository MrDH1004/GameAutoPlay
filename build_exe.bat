@echo off
rem 제어판을 exe로 묶는다 → dist\GameAutoPlay\GameAutoPlay.exe (파이썬 설치 없이 실행, 관리자 권한 자동 요청)
cd /d "%~dp0"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --uac-admin ^
  --name GameAutoPlay --icon assets\app.ico ^
  --add-data "templates;templates" --add-data "assets;assets" ^
  --add-data "config.yaml;." --add-data "farm_spots.json;." --add-data "tools\ocr.ps1;tools" ^
  --hidden-import bot2.brain --hidden-import gui_extra --hidden-import skill_guide --hidden-import version ^
  gui.py
echo.
echo 완료: dist\GameAutoPlay\GameAutoPlay.exe
