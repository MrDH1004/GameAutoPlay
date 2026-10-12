@echo off
chcp 65001 >nul
rem Claude 제어 브리지: 관리자 권한으로 다시 실행
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m tools.bridge
pause
