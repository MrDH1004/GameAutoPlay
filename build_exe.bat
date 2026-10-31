@echo off
chcp 65001 >nul
rem 제어판을 exe로 묶는다 → dist\버전\GameAutoPlay\GameAutoPlay.exe (일반 권한 실행, 입력은 Cua Driver 담당)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python 개발 환경이 없습니다. README의 설치 방법을 확인하세요.
  exit /b 1
)
for /f %%V in ('.venv\Scripts\python.exe -c "import version; print(version.VERSION)"') do set "RELEASE_VERSION=%%V"
set "CUA_EXE="
for /f "delims=" %%C in ('where cua-driver 2^>nul') do if not defined CUA_EXE set "CUA_EXE=%%C"
if not defined CUA_EXE (
  echo Cua Driver 실행 파일을 찾지 못했습니다. Cua Driver를 설치한 뒤 다시 빌드하세요.
  exit /b 1
)
for %%D in ("%CUA_EXE%") do set "CUA_BIN=%%~dpD"
for %%F in (cua-driver.exe cua-driver-uia.exe cua-cursor-theme.exe) do if not exist "%CUA_BIN%%%F" (
  echo Cua Driver 구성 파일이 없습니다: %CUA_BIN%%%F
  exit /b 1
)
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed ^
  --distpath "dist\%RELEASE_VERSION%" ^
  --name GameAutoPlay --icon assets\app.ico ^
  --add-data "templates;templates" --add-data "assets;assets" ^
  --add-data "config.yaml;." --add-data "farm_spots.json;." --add-data "tools\ocr.ps1;tools" ^
  --add-binary "%CUA_BIN%cua-driver.exe;cua-driver" ^
  --add-binary "%CUA_BIN%cua-driver-uia.exe;cua-driver" ^
  --add-binary "%CUA_BIN%cua-cursor-theme.exe;cua-driver" ^
  --hidden-import bot2.brain --hidden-import gui_extra --hidden-import skill_guide --hidden-import gear_guide --hidden-import updater --hidden-import version ^
  --copy-metadata PySide6 --copy-metadata mss --copy-metadata numpy --copy-metadata opencv-python ^
  --copy-metadata PyYAML ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebEngineQuick --exclude-module PySide6.QtPdf --exclude-module PySide6.QtPdfWidgets ^
  gui.py
if errorlevel 1 (
  echo 빌드 실패: 위 오류를 확인하세요.
  exit /b 1
)
echo.
echo 완료: dist\%RELEASE_VERSION%\GameAutoPlay\GameAutoPlay.exe
