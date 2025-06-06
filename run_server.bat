@echo off
echo Starting Django Application...
echo.

REM Ensure we are in the directory where the .bat file is located
cd /D "%~dp0"

echo Activating virtual environment...
call .\venv\Scripts\activate.bat

IF "%VIRTUAL_ENV%"=="" (
    echo ERROR: Virtual environment NOT activated.
    echo Please ensure 'venv' folder exists and 'activate.bat' is correct.
    pause
    exit /b 1
)
echo Virtual environment activated.
echo.

REM --- Attempt 1: Launch the browser using 'start' ---
echo Launching web browser to http://localhost:8000/dashboard (using start)...
start "" "http://localhost:8000/dashboard"
REM The empty "" after start is a good practice if the URL/path could have spaces,
REM it acts as a dummy title for the new window/process.

REM --- If the above 'start' command doesn't work, you can try 'explorer' ---
REM --- To try it, put 'REM' in front of the 'start "" "http://..."' line above ---
REM --- and remove 'REM' from the 'explorer "http://..."' line below. ---
REM explorer "http://localhost:8000/dashboard"
REM echo Launching web browser to http://localhost:8000/dashboard (using explorer)...

echo.
echo Launching server on http://localhost:8000/dashboard
echo (This window will show server logs. Press Ctrl+C to stop the server.)
echo.

REM *** CRITICAL: EDIT THE LINE BELOW WITH YOUR ACTUAL PROJECT NAME ***
waitress-serve --host 127.0.0.1 --port 8000 mcms_project.wsgi:application
REM ******************************************************************

echo.
echo Server has stopped or failed to start.
pause