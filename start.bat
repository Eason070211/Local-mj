@echo off
chcp 65001 >nul
cd /d "%~dp0"
title TileMind Server
echo ============================================
echo   TileMind - Offline Mahjong
echo   URL:  http://127.0.0.1:8639
echo   Close this window (or press Ctrl+C) to stop.
echo ============================================
echo.
python server.py --port 8639
echo.
echo Server stopped.
pause
