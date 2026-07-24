@echo off
setlocal EnableDelayedExpansion
:: delegation-core uninstaller — Windows
:: Double-click to run.
::
:: Removes the venv, config, logs, sessions, process tracker, graphs, hooks/
:: docs, the Scheduled Task autostart entry, and the installed dashboard app.
::
:: NEVER touches: your Obsidian vault, or downloaded model weights under
:: %USERPROFILE%\.delegation_core\models\. Those are yours regardless of
:: whether delegation-core itself stays installed.

set "CFG_DIR=%USERPROFILE%\.delegation_core"
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo.
echo  +-------------------------------+
echo  ^|  delegation-core uninstaller ^|
echo  +-------------------------------+
echo.

if not exist "%CFG_DIR%" (
    echo  Nothing to uninstall - %CFG_DIR% does not exist.
    pause
    exit /b 0
)

:: ── Read the vault path before anything is touched, purely to tell the user
:: where it still lives afterward. Never write to it. ────────────────────────
set "VAULT_PATH="
if exist "%CFG_DIR%\config.json" (
    for /f "delims=" %%V in ('python -c "import json;print(json.load(open(r'%CFG_DIR%\config.json')).get('vault_path',''))" 2^>nul') do set "VAULT_PATH=%%V"
)

:: ── Hard safety check: if the vault itself resolves to %CFG_DIR% or somewhere
:: inside it (e.g. a user who typed "%%USERPROFILE%%\.delegation_core" or a
:: subfolder of it as their vault path during setup), the targeted removals
:: below are not safe — several of them (sessions\, config.json, etc.) are
:: named exactly like things a real vault could contain. Refuse rather than
:: risk it. ────────────────────────────────────────────────────────────────
if defined VAULT_PATH (
    set "VAULT_UNDER_CFG="
    for /f "delims=" %%X in ('python -c "import os; v=os.path.realpath(os.path.expanduser(r'%VAULT_PATH%')); c=os.path.realpath(os.path.expanduser(r'%CFG_DIR%')); print('1' if (v==c or v.startswith(c+os.sep)) else '')" 2^>nul') do set "VAULT_UNDER_CFG=%%X"
    if defined VAULT_UNDER_CFG (
        echo ERROR: your configured vault path is %VAULT_PATH%,
        echo which is the same as ^(or lives inside^) %CFG_DIR%.
        echo.
        echo This uninstaller removes several specifically-named items inside
        echo %CFG_DIR% ^(sessions\, config.json, graphs\, ...^) - if the vault
        echo lives there too, that removal could delete real vault content.
        echo.
        echo Move your vault outside of %CFG_DIR% ^(and update vault_path in
        echo config.json^) before uninstalling, or remove things manually.
        pause
        exit /b 1
    )
)

:: ── Detect an installed dashboard app (Start Menu shortcut is the reliable
:: signal on Windows; MSI/NSIS installers don't leave a simple registry name
:: this script can query without extra tooling). ─────────────────────────────
set "DASH_FOUND="
if exist "%ProgramFiles%\delegation-core Dashboard" set "DASH_FOUND=1"
if exist "%LocalAppData%\Programs\delegation-core Dashboard" set "DASH_FOUND=1"

:: ── Skills that may have been installed by install.bat — list only, never
:: removed automatically (general Claude Code layer, not delegation-core-
:: specific). Best-effort: only checked if run from the original checkout. ──
set "FOUND_SKILLS="
if exist "%SCRIPT_DIR%\skills" if exist "%USERPROFILE%\.claude\skills" (
    for /d %%S in ("%SCRIPT_DIR%\skills\*") do (
        if exist "%USERPROFILE%\.claude\skills\%%~nxS" set "FOUND_SKILLS=!FOUND_SKILLS! %%~nxS"
    )
)

echo This will remove:
echo   - Python venv             %CFG_DIR%\venv
echo   - Config                  %CFG_DIR%\config.json
echo   - Logs                    %CFG_DIR%\*.log
echo   - Client session files    %CFG_DIR%\sessions\
echo   - Process tracker         %CFG_DIR%\processes.json
echo   - Code graphs             %CFG_DIR%\graphs\, graphs_registry.json
echo   - llama.cpp binary dir    %CFG_DIR%\llama\  (not the model weights - see below)
echo   - Hooks + agent docs      %CFG_DIR%\hooks\, AGENT_GUIDE*.md, CLAUDE_SYSTEM_PROMPT*.md
echo   - Misc state              last_brief.json, vault_health.json, backups_pre_upgrade_*
echo   - Autostart entry         Scheduled Task 'delegation-core-llama'
if defined DASH_FOUND (
    echo   - Installed dashboard app  (uninstall via Windows "Apps ^& features")
)
echo.
echo This will NEVER touch:
if defined VAULT_PATH (
    echo   - Your vault              !VAULT_PATH!
) else (
    echo   - Your vault              ^(unknown - config.json missing or unreadable^)
)
echo   - Downloaded model weights %CFG_DIR%\models\
echo.
if defined FOUND_SKILLS (
    echo NOTE: these bundled skills are present in %USERPROFILE%\.claude\skills and
    echo will NOT be removed ^(general Claude Code layer, not delegation-core-specific^):
    for %%N in (!FOUND_SKILLS!) do echo     - %%N
    echo   Remove manually if you want: rmdir /S /Q "%USERPROFILE%\.claude\skills\NAME"
    echo.
)

set /p CONFIRM="Type 'yes' to proceed: "
if /i not "%CONFIRM%"=="yes" (
    echo Aborted. Nothing was removed.
    pause
    exit /b 0
)
echo.

:: ── Stop and remove the autostart entry ──────────────────────────────────────
echo Removing autostart entry...
schtasks /end /tn "delegation-core-llama" >nul 2>&1
schtasks /delete /tn "delegation-core-llama" /f >nul 2>&1
echo   OK

:: ── Point at the installed dashboard app, but let Windows own the actual
:: removal — MSI/NSIS installers register a real uninstaller; running that
:: is more correct than trying to reverse-engineer it here. ──────────────────
if defined DASH_FOUND (
    echo.
    echo NOTE: an installed dashboard app was detected. Remove it via:
    echo   Settings ^> Apps ^> Apps ^& features ^> "delegation-core Dashboard" ^> Uninstall
)

:: ── Remove everything else. NEVER touch config.json's vault_path, and NEVER
:: touch %CFG_DIR%\models\. ──────────────────────────────────────────────────
echo.
echo Removing venv, config, logs, and remaining state...
rmdir /S /Q "%CFG_DIR%\venv" >nul 2>&1
del /Q "%CFG_DIR%\config.json" >nul 2>&1
del /Q "%CFG_DIR%\*.log" >nul 2>&1
rmdir /S /Q "%CFG_DIR%\sessions" >nul 2>&1
del /Q "%CFG_DIR%\processes.json" >nul 2>&1
rmdir /S /Q "%CFG_DIR%\graphs" >nul 2>&1
del /Q "%CFG_DIR%\graphs_registry.json" >nul 2>&1
rmdir /S /Q "%CFG_DIR%\llama" >nul 2>&1
rmdir /S /Q "%CFG_DIR%\hooks" >nul 2>&1
del /Q "%CFG_DIR%\AGENT_GUIDE.md" "%CFG_DIR%\AGENT_GUIDE.dist.md" >nul 2>&1
del /Q "%CFG_DIR%\CLAUDE_SYSTEM_PROMPT.md" "%CFG_DIR%\CLAUDE_SYSTEM_PROMPT.dist.md" >nul 2>&1
del /Q "%CFG_DIR%\last_brief.json" "%CFG_DIR%\vault_health.json" >nul 2>&1
for /d %%D in ("%CFG_DIR%\backups_pre_upgrade_*") do rmdir /S /Q "%%D" >nul 2>&1
echo   OK
echo.

echo Uninstall complete.
if defined VAULT_PATH (
    echo Your vault was left untouched at: !VAULT_PATH!
) else (
    echo Your vault ^(path unknown - config.json was missing^) was not touched.
)
echo Downloaded model weights were left untouched at: %CFG_DIR%\models\
pause
