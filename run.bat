@echo off
title AuraVision - NSUT Devlympics 2026 Launcher
echo ============================================================
echo   AuraVision Edge Processing System
echo   NSUT Devlympics Edition
echo ============================================================
echo.
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found in PATH!
    echo Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

echo Starting AuraVision processing node...
python auravision.py
pause
