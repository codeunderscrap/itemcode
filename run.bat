@echo off
title Item Code Studio
cd /d "%~dp0"
if not exist "data\itemcode.db" (
  echo First run - building the dictionary from the source workbooks...
  python seed.py
)
python server.py
pause
