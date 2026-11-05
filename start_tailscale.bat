@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Video localization (Tailscale)

rem ---- 1. Tim Tailscale va dia chi cua may nay trong mang Tailscale
where tailscale >nul 2>nul
if errorlevel 1 (
  if exist "C:\Program Files\Tailscale\tailscale.exe" (
    set "PATH=%PATH%;C:\Program Files\Tailscale"
  ) else (
    echo Khong tim thay Tailscale. Hay cai tai https://tailscale.com/download , dang nhap, roi chay lai file nay.
    pause
    exit /b 1
  )
)
set "TSIP="
for /f "delims=" %%i in ('tailscale ip -4 2^>nul') do if not defined TSIP set "TSIP=%%i"
if not defined TSIP (
  echo Tailscale chua ket noi. Mo ung dung Tailscale, dang nhap, bat "Connected", roi chay lai file nay.
  pause
  exit /b 1
)

rem ---- 2. Python va thu vien (giong start.bat)
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

rem ---- 3. Ma truy cap (tao mot lan, luu o access_token.txt)
if not exist access_token.txt python -c "import secrets;print(secrets.token_urlsafe(18))" > access_token.txt
set /p ACCESS_TOKEN=<access_token.txt

rem ---- 4. Chi lang nghe tren dia chi Tailscale (khong mo ra mang nha hay Internet)
set "HOST=%TSIP%"
set "NO_BROWSER=1"
if not defined PORT set "PORT=8765"
set "LINK=http://%TSIP%:%PORT%/?token=%ACCESS_TOKEN%"
> link.txt echo %LINK%
echo.
echo ================================================================
echo  MO LINK NAY TREN IPAD/DIEN THOAI (da cai Tailscale, cung tai khoan):
echo.
echo  %LINK%
echo.
echo  (link cung duoc luu o file link.txt; dong cua so nay de tat app)
echo ================================================================
echo.

:loop
python app.py
echo.
echo App da dung. Tu chay lai sau 5 giay (dong cua so nay de tat hoan toan)...
timeout /t 5 >nul
goto loop
