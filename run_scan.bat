@echo off
cd /d "%~dp0"
python -u lead_scanner.py >> scan_log.txt 2>&1
