@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not defined ALLOWED_ORIGINS set ALLOWED_ORIGINS=https://video-localization.vercel.app
where python >nul 2>nul
if errorlevel 1 (
  echo Khong tim thay Python. Cai Python 3.10 tro len tai https://www.python.org/downloads/ ^(nho tick "Add python.exe to PATH"^).
  pause
  exit /b 1
)
if not exist .venv (
  echo Lan dau chay: dang tao moi truong va cai thu vien, vui long doi...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
python app.py
pause
