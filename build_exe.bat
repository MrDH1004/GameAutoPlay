@echo off
chcp 65001 >nul
rem 제어판을 exe로 묶는다 → dist\GameAutoPlay\GameAutoPlay.exe (파이썬 설치 없이 실행, 관리자 권한 자동 요청)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python 개발 환경이 없습니다. README의 설치 방법을 확인하세요.
  exit /b 1
)
for /f %%V in ('.venv\Scripts\python.exe -c "import version; print(version.VERSION)"') do set "RELEASE_VERSION=%%V"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --uac-admin ^
  --distpath "dist\%RELEASE_VERSION%" ^
  --name GameAutoPlay --icon assets\app.ico ^
  --add-data "templates;templates" --add-data "assets;assets" ^
  --add-data "config.yaml;." --add-data "farm_spots.json;." --add-data "tools\ocr.ps1;tools" ^
  --hidden-import bot2.brain --hidden-import gui_extra --hidden-import skill_guide --hidden-import gear_guide --hidden-import version ^
  --copy-metadata PySide6 --copy-metadata mss --copy-metadata numpy --copy-metadata opencv-python ^
  --copy-metadata PyYAML --copy-metadata pydirectinput ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebEngineQuick --exclude-module PySide6.QtPdf --exclude-module PySide6.QtPdfWidgets ^
  gui.py
if errorlevel 1 (
  echo 빌드 실패: 위 오류를 확인하세요.
  exit /b 1
)
echo.
echo 완료: dist\%RELEASE_VERSION%\GameAutoPlay\GameAutoPlay.exe
