@echo off
chcp 65001 >nul
echo.
echo ============================================================
echo    YouTube Studio 分析数据抓取工具 - 安装
echo ============================================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未检测到 Python，请先安装 Python 3.9+
    echo    下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo ✅ Python 已安装
python --version
echo.

REM 创建虚拟环境
echo 📦 正在创建虚拟环境...
if not exist ".venv" (
    python -m venv .venv
)

REM 安装依赖
echo 📥 正在安装依赖...
call .venv\Scripts\activate.bat
pip install --upgrade pip -q
pip install playwright -q

echo.
echo ============================================================
echo    ✅ 安装完成！
echo.
echo    使用方法:
echo    1. 双击 start_chrome.bat 启动 Chrome
echo    2. 在 Chrome 中登录 YouTube Studio
echo    3. 进入 分析 > 内容 页面
echo    4. 双击 run_scraper.bat 导出数据
echo ============================================================
echo.
pause
