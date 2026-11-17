@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Zhiwei NEO launcher (ASCII only - cmd parses .bat with GBK,
rem  so Chinese comments here would corrupt the script)
rem  Double-click to start. Closing this window stops service.
rem ============================================================

set PORT=8000
set URL=http://127.0.0.1:%PORT%

rem ---- 1. already running? just open the browser ----
netstat -ano | findstr /c:":%PORT%" | findstr /c:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [Zhiwei NEO] Server already running on port %PORT%, opening browser.
    start "" "%URL%"
    timeout /t 2 >nul
    exit /b 0
)

rem ---- 2. locate a python interpreter that really has the deps ----
rem There may be several Pythons installed; only one that can
rem "import fastapi,uvicorn,numpy" counts.
set PY=
for %%c in ("python.exe" "py.exe") do (
    if not defined PY (
        where %%~c >nul 2>&1 && (
            %%~c -c "import fastapi,uvicorn,numpy" >nul 2>&1 && set PY=%%~c
        )
    )
)
rem fallback: well-known install locations
if not defined PY (
    for %%p in (
        "D:\Python\python.exe"
        "D:\Miniconda\python.exe"
        "%LocalAppData%\Programs\Python\Python313\python.exe"
        "%LocalAppData%\Programs\Python\Python312\python.exe"
        "%LocalAppData%\Programs\Python\Python311\python.exe"
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
    ) do (
        if not defined PY (
            if exist %%p (
                %%p -c "import fastapi,uvicorn,numpy" >nul 2>&1 && set PY=%%p
            )
        )
    )
)
if not defined PY (
    echo [Zhiwei NEO] Python with fastapi/uvicorn/numpy not found.
    echo Install Python 3.10+ then run: pip install fastapi uvicorn numpy
    pause
    exit /b 1
)

rem ---- 3. first run needs data + models ----
if not exist "data\georec.db" (
    echo [Zhiwei NEO] Dataset not found, generating demo data and training models ...
    "%PY%" run_pipeline.py
    if errorlevel 1 (
        echo [Zhiwei NEO] Pipeline failed. See messages above.
        pause
        exit /b 1
    )
)

rem ---- 4. open browser a few seconds later, then run server ----
start "" cmd /c "timeout /t 4 >nul & start "" %URL%"

echo ============================================================
echo  Zhiwei NEO  ^|  %URL%
echo  Press Ctrl+C or close this window to stop the server.
echo ============================================================
echo.

"%PY%" -m uvicorn app.api:app --host 127.0.0.1 --port %PORT%

echo.
echo [Zhiwei NEO] Server stopped. Press any key to close.
pause >nul
