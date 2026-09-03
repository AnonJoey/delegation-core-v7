@echo off
setlocal EnableDelayedExpansion
rem delegation-core installer: Windows
rem Double-click to run. Detects Python, installs dependencies,
rem creates venv, installs package, then launches the setup wizard.

chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "VENV=%USERPROFILE%\.delegation_core\venv"
set "SCRIPT_DIR=%~dp0"
rem Remove trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo.
echo  +-------------------------------+
echo  ^|  delegation-core  installer  ^|
echo  +-------------------------------+
echo.

rem == 1. Find Python 3.11+ ===================================================
echo  Checking Python...

set "PYTHON="

rem Try Python Launcher (py.exe) first: handles multiple installs on Windows
rem Execute python code check to ensure the version is truly installed and working
where py >nul 2>&1
if not errorlevel 1 (
    for %%v in (3.13 3.12 3.11) do (
        if not defined PYTHON (
            py -%%v -c "import sys; assert sys.version_info>=(3,11)" >nul 2>&1
            if not errorlevel 1 (
                set "PYTHON=py -%%v"
            )
        )
    )
)

rem Fall back to python / python3 in PATH
if not defined PYTHON (
    for %%c in (python python3) do (
        if not defined PYTHON (
            %%c -c "import sys; assert sys.version_info>=(3,11)" >nul 2>&1
            if not errorlevel 1 set "PYTHON=%%c"
        )
    )
)

if not defined PYTHON (
    echo.
    echo  ERROR: Python 3.11 or newer is required.
    echo.
    echo  Download from: https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during installation.
    echo.
    echo  After installing Python, run this installer again.
    echo.

    rem Try to open the download page automatically
    start "" "https://www.python.org/downloads/"

    pause
    exit /b 1
)

rem Avoid nested double quotes inside the -c argument: cmd's own command-line
rem tokenizer (this runs through cmd /c to capture output) doesn't treat \"
rem as an escaped quote the way it looks like it should, so an f-string with
rem embedded double quotes here can arrive at Python mangled. Single quotes
rem inside a double-quoted -c argument avoid the ambiguity entirely.
for /f "delims=" %%v in ('%PYTHON% -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))"') do set "PY_VER=%%v"
echo    OK: Python !PY_VER!  ^(!PYTHON!^)
echo.

rem == 2. Visual C++ check (informational) =====================================
rem Most users already have this via Office, Teams, or Windows itself.
rem Pre-built Python wheels mean this is rarely needed: just note if absent.
echo  Checking Visual C++ Redistributable...
reg query "HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64" >nul 2>&1
if errorlevel 1 (
    reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\X64" >nul 2>&1
)
if errorlevel 1 (
    echo    Not detected ^(usually not required, continuing^)
) else (
    echo    OK
)
echo.

rem == 3. Virtual environment =================================================
echo  Creating virtual environment at %VENV%...
%PYTHON% -m venv "%VENV%"
if errorlevel 1 (
    echo  ERROR: Could not create virtual environment.
    pause
    exit /b 1
)
echo    OK
echo.

rem == 4. Install package =====================================================
echo  Installing delegation-core and Python dependencies...
echo  ^(sentence-transformers and chromadb are large: may take a few minutes^)
echo.
rem v5.1 patch: pin setuptools<82. torch (via sentence-transformers) requires
rem setuptools<82; an unpinned upgrade grabs 82.x and breaks the torch import.
"%VENV%\Scripts\pip" install --quiet --upgrade pip wheel "setuptools<82"
"%VENV%\Scripts\pip" install "%SCRIPT_DIR%[graph,web]" 2>nul
if errorlevel 1 (
    rem Fallback if extra resolution fails
    "%VENV%\Scripts\pip" install "%SCRIPT_DIR%"
)
if errorlevel 1 (
    echo  ERROR: Installation failed. Check the messages above.
    pause
    exit /b 1
)
echo.
echo    Installation complete.
echo.

rem == 4b-5. Finish the install ==============================================
rem Everything from here used to be ~145 more lines of batch, transcribed from
rem install.sh and already drifted from it: this file refreshed the MCP client
rem config on an upgrade and install.sh did not, and neither of them ever
rem repaired the Claude Desktop entry. It now lives in
rem delegation_core/installer.post_install(), shared by the three platforms:
rem agent docs and hooks, bundled skills, the Tauri dashboard, the health
rem cache, the service registration, and the client configs. It also launches
rem the setup wizard when this turns out to be a fresh install.
echo  Finishing the install...
"%VENV%\Scripts\delegation-core" post-install --root "%SCRIPT_DIR%"
if errorlevel 1 (
    echo.
    echo  WARNING: the finishing step reported a problem. The package itself is
    echo  installed; rerun it with:
    echo    "%VENV%\Scripts\delegation-core" post-install --root "%SCRIPT_DIR%"
)
echo.
pause
