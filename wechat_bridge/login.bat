@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==============================================
echo   小满 x 微信 ClawBot  ——  扫码登录
echo   用微信「扫一扫」扫下面二维码，手机上确认
echo ==============================================
echo.
node index.js login
if errorlevel 1 goto :end
echo.
echo 登录成功，正在重启微信桥切换到新账号...
powershell -NoProfile -Command "Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force"
timeout /t 2 /nobreak >nul
wscript //nologo "%~dp0start.vbs"
echo 微信桥已重启，去新微信里给小满发消息吧～
:end
echo.
pause
