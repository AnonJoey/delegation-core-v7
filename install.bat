@echo off
setlocal EnableDelayedExpansion
:: delegation-core installer — Windows
:: Double-click to run. Detects Python, installs dependencies,
:: creates venv, installs package, then launches the setup wizard.

set "VENV=%USERPROFILE%\.delegation_core\venv"
set "SCRIPT_DIR=%~dp0"
:: Remove trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo.
echo  +-------------------------------+
echo  ^|  delegation-core  installer  ^|
echo  +-------------------------------+
echo.

:: ── 1. Find Python 3.11+ ────────────────────────────────────────────────────
echo  Checking Python...

set "PYTHON="

:: Try Python Launcher (py.exe) first — handles multiple installs on Windows
where py >nul 2>&1
if not errorlevel 1 (
    for %%v in (3.13 3.12 3.11) do (
        if not defined PYTHON (
            py -%%v --version >nul 2>&1
            if not errorlevel 1 (
                set "PYTHON=py -%%v"
            )
        )
    )
)

:: Fall back to python / python3 in PATH
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

    :: Try to open the download page automatically
    start "" "https://www.python.org/downloads/"

    pause
    exit /b 1
)

for /f "delims=" %%v in ('%PYTHON% -c "import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")"') do set "PY_VER=%%v"
echo    OK: Python !PY_VER!  ^(!PYTHON!^)
echo.

:: ── 2. Visual C++ check (informational) ─────────────────────────────────────
:: Most users already have this via Office, Teams, or Windows itself.
:: Pre-built Python wheels mean this is rarely needed — just note if absent.
echo  Checking Visual C++ Redistributable...
reg query "HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64" >nul 2>&1
if errorlevel 1 (
    reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\X64" >nul 2>&1
)
if errorlevel 1 (
    echo    Not detected ^(usually not required — continuing^)
) else (
    echo    OK
)
echo.

:: ── 3. Virtual environment ───────────────────────────────────────────────────
echo  Creating virtual environment at %VENV%...
%PYTHON% -m venv "%VENV%"
if errorlevel 1 (
    echo  ERROR: Could not create virtual environment.
    pause
    exit /b 1
)
echo    OK
echo.

:: ── 4. Install package ───────────────────────────────────────────────────────
echo  Installing delegation-core and Python dependencies...
echo  ^(sentence-transformers and chromadb are large — may take a few minutes^)
echo.
rem v5.1 patch: pin setuptools<82. torch (via sentence-transformers) requires
rem setuptools<82; an unpinned upgrade grabs 82.x and breaks the torch import.
"%VENV%\Scripts\pip" install --quiet --upgrade pip wheel "setuptools<82"
"%VENV%\Scripts\pip" install "%SCRIPT_DIR%"
if errorlevel 1 (
    echo  ERROR: Installation failed. Check the messages above.
    pause
    exit /b 1
)
echo.
echo    Installation complete.
echo.

:: ── 4b. Copy agent docs and hooks to a stable location ──────────────────────
:: independent of where this project folder ends up (the wizard wires
:: Claude Code/Desktop up to these paths).
:: Portability guard: never clobber a doc the user customized — if one exists,
:: keep theirs and drop the shipped copy alongside as <name>.dist.md.
echo  Installing agent docs and hooks to %%USERPROFILE%%\.delegation_core...
if not exist "%USERPROFILE%\.delegation_core\hooks" mkdir "%USERPROFILE%\.delegation_core\hooks"
if exist "%USERPROFILE%\.delegation_core\AGENT_GUIDE.md" (
    copy /Y "%SCRIPT_DIR%\AGENT_GUIDE.md" "%USERPROFILE%\.delegation_core\AGENT_GUIDE.dist.md" >nul 2>&1
    echo    - AGENT_GUIDE.md already present - kept yours; shipped copy saved as AGENT_GUIDE.dist.md
) else (
    copy /Y "%SCRIPT_DIR%\AGENT_GUIDE.md" "%USERPROFILE%\.delegation_core\" >nul 2>&1
)
if exist "%USERPROFILE%\.delegation_core\CLAUDE_SYSTEM_PROMPT.md" (
    copy /Y "%SCRIPT_DIR%\CLAUDE_SYSTEM_PROMPT.md" "%USERPROFILE%\.delegation_core\CLAUDE_SYSTEM_PROMPT.dist.md" >nul 2>&1
    echo    - CLAUDE_SYSTEM_PROMPT.md already present - kept yours; shipped copy saved as CLAUDE_SYSTEM_PROMPT.dist.md
) else (
    copy /Y "%SCRIPT_DIR%\CLAUDE_SYSTEM_PROMPT.md" "%USERPROFILE%\.delegation_core\" >nul 2>&1
)
copy /Y "%SCRIPT_DIR%\hooks\*.py" "%USERPROFILE%\.delegation_core\hooks\" >nul 2>&1
echo    OK
echo.

:: ── 4c. Install bundled Claude skills to %USERPROFILE%\.claude\skills ─────────
:: Personal skills are available in every Claude Code session on this machine,
:: independent of plugin config. Guard: never clobber a skill already present.
if exist "%SCRIPT_DIR%\skills" (
    echo  Installing bundled skills to %USERPROFILE%\.claude\skills...
    if not exist "%USERPROFILE%\.claude\skills" mkdir "%USERPROFILE%\.claude\skills"
    for /d %%S in ("%SCRIPT_DIR%\skills\*") do (
        if exist "%USERPROFILE%\.claude\skills\%%~nxS" (
            echo    - %%~nxS already present - kept yours
        ) else (
            xcopy /E /I /Q /Y "%%S" "%USERPROFILE%\.claude\skills\%%~nxS" >nul
            echo    + %%~nxS
        )
    )
    echo    OK - skills available on next Claude Code session start.
    echo.
)

:: ── 4d. Install the Tauri dashboard app ──────────────────────────────────────
:: Prefer a bundle already built locally (dev/CI convenience); otherwise fall
:: back to the latest GitHub release via `gh` if it's installed. Never fail the
:: whole install over this — a missing dashboard just prints manual build
:: instructions, since the Python/MCP side above is what matters most.
echo  Installing delegation-core Dashboard app...
set "DASH_BUNDLE_DIR=%SCRIPT_DIR%\dashboard\src-tauri\target\release\bundle"
set "MSI_FILE="
set "NSIS_FILE="

if exist "%DASH_BUNDLE_DIR%\msi\*.msi" (
    for %%F in ("%DASH_BUNDLE_DIR%\msi\*.msi") do if not defined MSI_FILE set "MSI_FILE=%%F"
)
if exist "%DASH_BUNDLE_DIR%\nsis\*.exe" (
    for %%F in ("%DASH_BUNDLE_DIR%\nsis\*.exe") do if not defined NSIS_FILE set "NSIS_FILE=%%F"
)

if not defined MSI_FILE if not defined NSIS_FILE (
    where gh >nul 2>&1
    if not errorlevel 1 (
        echo    No local dashboard build found - checking the latest GitHub release...
        set "DASH_TMP=%TEMP%\delegation_core_dashboard_dl"
        rmdir /S /Q "!DASH_TMP!" >nul 2>&1
        mkdir "!DASH_TMP!" >nul 2>&1
        gh release download --repo AnonJoey/delegation-core-v6.4 --pattern "*.msi" --dir "!DASH_TMP!" >nul 2>&1
        if exist "!DASH_TMP!\*.msi" (
            for %%F in ("!DASH_TMP!\*.msi") do if not defined MSI_FILE set "MSI_FILE=%%F"
        ) else (
            gh release download --repo AnonJoey/delegation-core-v6.4 --pattern "*.exe" --dir "!DASH_TMP!" >nul 2>&1
            for %%F in ("!DASH_TMP!\*.exe") do if not defined NSIS_FILE set "NSIS_FILE=%%F"
        )
    )
)

if defined MSI_FILE (
    echo    Installing !MSI_FILE! ...
    msiexec /i "!MSI_FILE!" /qb
    echo    OK - find "delegation-core Dashboard" in your Start Menu.
) else if defined NSIS_FILE (
    echo    Installing !NSIS_FILE! ...
    "!NSIS_FILE!" /S
    echo    OK - find "delegation-core Dashboard" in your Start Menu.
) else (
    echo    No dashboard build found locally or on GitHub releases.
    echo    Build it manually:  cd dashboard ^&^& npm install ^&^& npm run tauri build
)
echo.

:: Invalidate cached health so the corrected recursive metric recomputes.
del /Q "%USERPROFILE%\.delegation_core\vault_health.json" >nul 2>&1

:: ── 5. Launch wizard only on a FRESH install ─────────────────────────────────
:: On an existing deployment the wizard would re-prompt and could overwrite a
:: working config.json, so an upgrade must leave configuration untouched.
if exist "%USERPROFILE%\.delegation_core\config.json" (
    echo  Existing config.json detected - preserved. Skipping setup wizard.
    echo.
    echo  Upgrade complete. Restart delegation-core ^(or quit and reopen Claude^)
    echo  to load the new code.
    echo  To reconfigure manually later:  "%VENV%\Scripts\delegation-core" setup
) else (
    echo  Launching setup wizard...
    echo.
    "%VENV%\Scripts\delegation-core" setup
)
