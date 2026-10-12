@echo off
rem 제어판 실행 (관리자 권한은 gui.py가 알아서 요청한다)
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" gui.py --autostart
