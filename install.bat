@echo off
setlocal enabledelayedexpansion
title Protiviti AI Scribe — Setup
color 0B
cls

echo.
echo  +-------------------------------------------+
echo  ^|   Protiviti AI Scribe  ^|  Setup Wizard     ^|
echo  +-------------------------------------------+
echo.

:: ─── 1. Python check ───────────────────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [FAIL] Python not found on this machine.
    echo.
    echo  Install Python 3.10 or newer from:
    echo    https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK]   Python %PYVER% detected

:: ─── 2. pip packages ───────────────────────────────────────────────────────
echo.
echo  Installing Python packages (may take 1-2 min on first run)...
python -m pip install -r "%~dp0requirements.txt" --quiet --disable-pip-version-check
if %errorlevel% neq 0 (
    echo  [FAIL] pip install failed.
    echo         Check your internet connection and try again.
    pause
    exit /b 1
)
echo  [OK]   Python packages installed

:: ─── 3. Playwright Chromium ────────────────────────────────────────────────
echo.
echo  Installing Chromium browser for Playwright...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo  [FAIL] Playwright Chromium install failed.
    pause
    exit /b 1
)
echo  [OK]   Chromium browser installed

:: ─── 4. .env file ──────────────────────────────────────────────────────────
echo.
if not exist "%~dp0.env" (
    copy "%~dp0.env.example" "%~dp0.env" >nul
    echo  [OK]   Created .env file — edit it to add your API key
) else (
    echo  [OK]   .env already exists
)

:: ─── Done ──────────────────────────────────────────────────────────────────
echo.
echo  +-------------------------------------------+
echo  ^|          Setup Complete!                  ^|
echo  +-------------------------------------------+
echo.
echo  Required — do this before first use:
echo    1. Open .env  (in this folder)
echo    2. Set  GEMINI_API_KEY  to your free key from:
echo       https://aistudio.google.com/app/apikey
echo.
echo  Optional — to email MoM after meetings:
echo    3. Set SMTP_USER, SMTP_PASSWORD, EMAIL_TO in .env
echo       (see .env for instructions)
echo.
echo  To start the app:
echo    Double-click  launch.bat
echo    A microphone icon appears in your system tray.
echo.
pause
