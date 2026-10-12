@echo off
cd /d "%~dp0"
rem 사냥터 고정 모드 (관리자 PowerShell/명령 프롬프트에서 실행)
.venv\Scripts\python.exe run.py --farm
pause
