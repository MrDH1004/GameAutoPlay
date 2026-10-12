@echo off
chcp 65001 >nul
rem 게임이 관리자 권한으로 실행되므로 매크로도 관리자 권한으로 다시 실행한다
net session >nul 2>&1
if %errorlevel% neq 0 (
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    )
    exit /b
)
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py %*
pause
