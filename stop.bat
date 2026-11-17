@echo off
setlocal
set PORT=8000

echo [Zhiwei NEO] Looking for process listening on port %PORT% ...
set PID=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"LISTENING" ^| findstr /c:":%PORT%"') do set PID=%%p

if "%PID%"=="" (
    echo [Zhiwei NEO] No server running on port %PORT%.
    timeout /t 2 >nul
    exit /b 0
)

echo [Zhiwei NEO] Stopping PID %PID% ...
taskkill /PID %PID% /F
timeout /t 2 >nul
echo [Zhiwei NEO] Done.
