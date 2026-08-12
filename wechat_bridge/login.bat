@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==============================================
echo   小满 x 微信 ClawBot  ——  扫码登录
echo   用微信「扫一扫」扫下面二维码，手机上确认
echo ==============================================
echo.
node index.js login
echo.
pause
