@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ===== 小满微信桥状态 =====
powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',18888); $c.Close(); Write-Host '桥进程：运行中（端口 18888 在线）' -ForegroundColor Green}catch{Write-Host '桥进程：未运行' -ForegroundColor Red}"
Get-Process node -ErrorAction SilentlyContinue | Select-Object Id, StartTime | Format-Table -AutoSize
echo 最近日志（logs\wechat-bridge.log 末尾 15 行）：
Get-Content "..\logs\wechat-bridge.log" -Tail 15 -ErrorAction SilentlyContinue
echo.
pause
