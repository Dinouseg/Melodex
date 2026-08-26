@echo off
REM Start Melodex from source.
python app.py %*
if errorlevel 1 pause
