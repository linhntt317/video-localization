@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Cai dat tu khoi dong Video localization cung Windows ===
echo.
schtasks /create /tn "VideoLocalization" /tr "\"%~dp0start_tailscale.bat\"" /sc onlogon /f
if errorlevel 1 (
  echo Khong tao duoc tac vu tu khoi dong. Thu bam chuot phai file nay, chon "Run as administrator".
) else (
  echo Da tao tac vu tu chay moi khi ban dang nhap Windows.
)
echo.
echo Chinh may khong tu ngu khi cam sac (de iPad dung duoc bat cu luc nao)...
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 15
echo.
echo Xong. Luu y: may phai dang bat, dang nhap Windows va co mang. Dong nap laptop co the lam may ngu:
echo xem README muc "May nha + Tailscale" neu muon doi.
echo De go tu khoi dong: schtasks /delete /tn "VideoLocalization" /f
pause
