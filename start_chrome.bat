@echo off
chcp 65001 >nul
echo.
echo ============================================================
echo    启动 Chrome（调试模式）
echo ============================================================
echo.

REM 设置调试端口
set DEBUG_PORT=9222

REM 设置专用配置文件夹（不影响你正常的 Chrome）
set USER_DATA_DIR=%~dp0chrome_debug_profile

REM 查找 Chrome 路径
set "CHROME_PATH="

if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
)
if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    set "CHROME_PATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    set "CHROME_PATH=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
)

if "%CHROME_PATH%"=="" (
    echo ❌ 找不到 Chrome，请确保已安装 Google Chrome
    pause
    exit /b 1
)

echo ✅ 找到 Chrome: %CHROME_PATH%
echo.
echo 🚀 正在启动 Chrome（调试端口: %DEBUG_PORT%）...
echo.
echo ⚠️  注意事项:
echo    1. 这会打开一个独立的 Chrome 窗口
echo    2. 首次使用需要登录你的 Google 账号
echo    3. 登录状态会保存在本地，下次无需重新登录
echo.

start "" "%CHROME_PATH%" --remote-debugging-port=%DEBUG_PORT% --user-data-dir="%USER_DATA_DIR%" --no-first-run --no-default-browser-check

echo ✅ Chrome 已启动！
echo.
echo 下一步: 
echo    1. 在 Chrome 中登录并打开你的 YouTube Studio
echo    2. 导航到分析页面，设置好筛选条件
echo    3. 运行 run_scraper.bat 开始抓取数据
echo.
pause
