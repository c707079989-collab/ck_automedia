@echo off
chcp 65001 >nul
setlocal

set ROOT=%~dp0
set SAU=%ROOT%social-auto-upload
set PY=%SAU%\.venv\Scripts\python.exe

if not exist "%PY%" (
  echo [x] 未找到虚拟环境: %PY%
  echo     首次安装：cd social-auto-upload ^&^& python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)

echo [1/1] 启动 social-auto-upload 后端 (127.0.0.1:5409) ...
start "sau-backend" cmd /c "cd /d "%SAU%" && "%PY%" sau_backend.py"

timeout /t 3 >nul
echo.
echo   控制台 : http://127.0.0.1:5409
echo   爆款台 : http://127.0.0.1:5409/#/hot-analysis
echo.
echo   说明：v0.3 的 sau-bridge（8790）已归档到 _archive\sau-bridge-v0.4.tar.gz，
echo         发布与抓取现在全部由 social-auto-upload 直连完成，不再需要第二个进程。
echo.
endlocal
