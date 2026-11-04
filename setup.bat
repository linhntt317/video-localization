@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Video localization: kiem tra va cai dat ===
call :refresh
where python >nul 2>nul || (echo Cai Python... & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements)
where ffmpeg >nul 2>nul || (echo Cai ffmpeg... & winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements)
set "PATH=%PATH%;%USERPROFILE%\scoop\shims"
where tesseract >nul 2>nul || (where scoop >nul 2>nul && (echo Cai Tesseract bang scoop... & call scoop install tesseract))
where tesseract >nul 2>nul || (if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (echo Da co Tesseract) else (echo Cai Tesseract... & winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements))
call :refresh
if not exist tessdata mkdir tessdata
if not exist tessdata\eng.traineddata powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata -OutFile tessdata\eng.traineddata"
if not exist tessdata\chi_sim.traineddata powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata -OutFile tessdata\chi_sim.traineddata"
set "TESSDATA_PREFIX=%~dp0tessdata"
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set "PATH=%PATH%;C:\Program Files\Tesseract-OCR"
echo.
where python & where ffmpeg & where tesseract
echo.
echo === Khoi dong app ===
call start.bat
goto :eof
:refresh
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine')+';'+[Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%i"
exit /b
