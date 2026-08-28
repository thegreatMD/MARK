.
@echo off
setlocal enabledelayedexpansion
rem Do not use `chcp 65001` here. On Windows cmd it can drop the first
rem character of every following line (cd -> /d, echo -> cho, set -> et).

cd /d "%~dp0"

echo ========================================
echo   JARVIS / Mark Assistant - Launcher
echo ========================================
echo.

set "PYTHON_EXE="

for %%P in (
    "%~dp0venv\Scripts\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python310\python.exe"
) do (
    if exist %%~P (
        set "PYTHON_EXE=%%~P"
        goto :found_python
    )
)

for /f "delims=" %%i in ('where python 2^>nul') do (
    set "PYTHON_EXE=%%i"
    goto :found_python
)

echo [ERROR] Python interpreter not found.
echo Please install Python 3.10 or later from https://python.org
echo or update this script to point to your Python installation.
pause
exit /b 1

:found_python
echo [Python] Found: !PYTHON_EXE!

if "%~1"=="--no-setup" goto :skip_setup
if "%~1"=="-n" goto :skip_setup

echo.
echo [1/3] Setting up virtual environment...
if not exist "%~dp0venv\Scripts\python.exe" (
    echo   Creating venv...
    "!PYTHON_EXE!" -m venv venv
    if errorlevel 1 (
        echo [WARNING] Failed to create virtual environment; continuing with system Python.
    ) else (
        set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
        echo   Virtual environment created.
    )
)

echo.
echo [2/3] Installing dependencies...
if exist "%~dp0requirements.txt" (
    "!PYTHON_EXE!" -m pip install --upgrade pip
    "!PYTHON_EXE!" -m pip install -r "%~dp0requirements.txt"
    echo   Dependencies installed.
)

echo.
echo [3/3] Checking environment file...
if not exist "%~dp0.env" (
    if exist "%~dp0.env.example" (
        copy "%~dp0.env.example" "%~dp0.env"
        echo   Created .env from .env.example — please edit it before next run.
    )
)

:skip_setup
echo.
echo Launching Mark Assistant...
echo Dashboard will be available at http://localhost:8080
echo Press Ctrl+C to stop.
echo.

"!PYTHON_EXE!" "%~dp0Mark.py"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Mark Assistant exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
