@echo off
cd /d "%~dp0"

where py >nul 2>&1 && py -3 main.py && goto :eof
where python3 >nul 2>&1 && python3 main.py && goto :eof

if exist "C:\Users\SG108049\AppData\Local\Programs\Python\Python314\python.exe" (
    "C:\Users\SG108049\AppData\Local\Programs\Python\Python314\python.exe" main.py
    goto :eof
)

echo.
echo ERROR: Python 3 not found. Install Python 3.8+ and add it to PATH.
pause
