@echo off
chcp 65001 >nul
title 无人机法规监控 - 手机端服务
echo.
echo   无人机法规监控 · 手机端后端
echo   ====================================
echo   服务地址: http://127.0.0.1:8765/
echo   手机/MuMu 通过 adb reverse 访问
echo.
adb reverse tcp:8765 tcp:8765 >nul 2>&1
echo   [√] adb reverse 已配置（MuMu 第二实例 16416）
echo   [i] 保持本窗口开着，手机端才能访问
echo.
python "%~dp0server_mobile.py"
pause