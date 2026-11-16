@echo off
rem Run the in-development NeoTJAEditor from source (no build needed).
rem Keeps the window open on error so the message can be read.
cd /d "%~dp0NeoTJAEditor"
".venv\Scripts\python.exe" -m neotja
if errorlevel 1 pause
