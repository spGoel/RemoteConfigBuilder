@echo off
cd /d "%~dp0"

where py >nul 2>&1 && py -3 main.py && goto :eof
where python3 >nul 2>&1 && python3 main.py && goto :eof

echo.
echo ERROR: Python 3.8 or newer was not found.
echo Install Python and ensure the py launcher or python3 is available.
pause
