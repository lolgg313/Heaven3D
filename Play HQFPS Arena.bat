@echo off
rem Double-click to play the HQ FPS arsenal in the arena.
cd /d "%~dp0"
python Play_HQFPS_Arena.py %*
if errorlevel 1 pause
