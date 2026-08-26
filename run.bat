@echo off
REM Start WaveQueen Downloader from source.
python app.py %*
if errorlevel 1 pause
