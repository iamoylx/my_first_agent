@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小满微信桥

rem ===== 1) 登录检查：没登录就先扫码 =====
node -e "import('weixin-agent-sdk').then(m=>process.exit(m.isLoggedIn()?0:2)).catch(()=>process.exit(2))" >nul 2>nul
if errorlevel 2 (
  echo [提示] 还没有微信登录，先打开扫码窗口...
  call login.bat
)

rem ===== 2) 已在运行就跳过（端口 18888 = 推送服务在线 = 桥活着） =====
powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',18888); $c.Close(); exit 0}catch{exit 1}"
if not errorlevel 1 (
  echo [提示] 微信桥已在运行（端口 18888 在线），无需重复启动。
  pause
  exit /b 0
)

rem ===== 3) 隐藏后台启动 =====
echo 正在后台启动小满微信桥...
wscript //nologo "%~dp0start.vbs"

rem ===== 4) 自检 =====
powershell -NoProfile -Command "Start-Sleep -Seconds 4; try{$c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',18888); $c.Close(); Write-Host '小满微信桥已后台运行（端口 18888 在线）' -ForegroundColor Green}catch{Write-Host '启动失败：请查看 logs\wechat-bridge.err.log' -ForegroundColor Red}"
echo.
pause
