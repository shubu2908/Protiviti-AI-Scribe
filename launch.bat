@echo off
cd /d "%~dp0"
echo Starting Protiviti AI Scribe...
echo Look for the microphone icon in your system tray (bottom-right corner).
start "" pythonw tray_app.py
timeout /t 2 /nobreak >nul
