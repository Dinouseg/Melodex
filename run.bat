@echo off
REM Start Wavequen Downloader from source.
python app.py %*
if errorlevel 1 pause
