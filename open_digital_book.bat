@echo off
setlocal
cd /d "%~dp0"
python scripts\open_digital_book.py %*
if errorlevel 1 pause
