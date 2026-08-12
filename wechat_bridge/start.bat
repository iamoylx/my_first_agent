@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 检查是否已登录：未登录先跳去扫码
node -e "import('weixin-agent-sdk').then(m=>process.exit(m.isLoggedIn()?0:2)).catch(()=>process.exit(2))" >nul 2>nul
if errorlevel 2 (
  echo [提示] 还没有微信登录，先打开扫码窗口...
  call login.bat
)
echo 正在后台启动小满微信桥...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath 'node' -ArgumentList 'index.js','start' -WorkingDirectory (Get-Location) -WindowStyle Hidden -RedirectStandardOutput '..\logs\wechat-bridge.out.log' -RedirectStandardError '..\logs\wechat-bridge.err.log' -PassThru; Start-Sleep -Seconds 3; if ($p.HasExited) { Write-Host ('启动失败，请查看 logs\wechat-bridge.err.log') -ForegroundColor Red } else { Write-Host ('小满微信桥已后台运行 PID=' + $p.Id) -ForegroundColor Green }"
