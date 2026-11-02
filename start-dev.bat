@echo off
chcp 65001 >nul
title GEO 本地开发启动

set GEO_DIR=D:\Project\Geo\geo

echo ==============================================
echo   GEO 智能监测 - 本地开发
echo   后端: http://localhost:8088  (--reload 自动重载)
echo   前端: http://localhost:5173  (Vite 热更新)
echo ==============================================
echo.

echo [1/2] 启动后端...
start "GEO-Backend" cmd /k "call conda activate geo && cd /d %GEO_DIR%\backend && uvicorn app.main:app --reload --reload-dir app --port 8088"

echo [2/2] 启动前端...
start "GEO-Frontend" cmd /k "cd /d %GEO_DIR%\frontend && npm run dev"

echo.
echo 两个窗口已打开。改完代码保存后直接刷新浏览器即可，无需重启。
echo 3 秒后自动打开浏览器...
timeout /t 3 >nul
start http://localhost:5173
pause
