@echo off
chcp 65001 >nul
echo.
echo ============================================================
echo    YouTube Studio 数据导出工具
echo ============================================================
echo.

REM 查找 Python
set PYTHON=
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else if exist "..\.venv\Scripts\python.exe" (
    set PYTHON=..\.venv\Scripts\python.exe
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set PYTHON=python
    ) else (
        echo ❌ 未找到 Python
        pause
        exit /b 1
    )
)

echo 请确保已运行 start_chrome.bat 并登录 YouTube Studio
echo.
%PYTHON% youtube_export_final.py

pause
