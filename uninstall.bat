@echo off
setlocal
rem delegation-core uninstaller: Windows
rem Usage: double-click, or uninstall.bat [--yes] [--dry-run]
rem
rem A stub on purpose, with exactly two jobs Python cannot do for itself:
rem   1. find the interpreter, and
rem   2. delete the venv AFTER that interpreter has exited, which on Windows is
rem      the only moment it can be deleted at all.
rem
rem Everything else lives in delegation_core/installer.py and is shared with
rem Linux and macOS. The previous version of this file was 158 lines of batch
rem that had already drifted from its bash twin: every removal here ended in
rem ">nul 2>&1" with no errorlevel check, so a half-removed install printed OK.
rem It also never stopped the daemon first, which on Windows is precisely why
rem those removals failed: a running process holds its own files open.
rem
rem Plain ASCII throughout, and "rem" rather than "::" for comments. An em dash
rem or a box-drawing character in a "::" line breaks the cmd.exe parser for the
rem whole file, which is a defect this project has already catalogued.
rem
rem NEVER touched: your vault, or the model weights under
rem %USERPROFILE%\.delegation_core\models\.

set "CFG_DIR=%USERPROFILE%\.delegation_core"
set "VENV=%CFG_DIR%\venv"
set "EXE=%VENV%\Scripts\delegation-core.exe"
set "SENTINEL=%CFG_DIR%\.venv-pending-removal"

echo.
echo  +---------------------------------+
echo  ^|  delegation-core  uninstaller   ^|
echo  +---------------------------------+
echo.

if not exist "%CFG_DIR%" (
    echo Nothing to uninstall: %CFG_DIR% does not exist.
    pause
    exit /b 0
)

if not exist "%EXE%" (
    echo ERROR: %EXE% is missing.
    echo.
    echo Without it the uninstall cannot stop the daemon or unregister its
    echo services, and deleting files while those are live is what this script
    echo exists to avoid. If the install is already broken, remove it by hand:
    echo.
    echo   schtasks /delete /tn "delegation-core" /f
    echo   schtasks /delete /tn "delegation-core-llama" /f
    echo   rmdir /S /Q "%VENV%"
    echo.
    echo Your vault and %CFG_DIR%\models\ are not part of that.
    pause
    exit /b 1
)

rem The sentinel is the handshake. Python writes it only when it has actually
rem finished removing state and the venv is the one thing still standing.
rem Clearing it first means a stale one cannot authorise a deletion this run
rem did not ask for.
if exist "%SENTINEL%" del /Q "%SENTINEL%"

"%EXE%" uninstall %*
set "CODE=%ERRORLEVEL%"

rem Deliberately NOT keyed on ERRORLEVEL 0: exit 0 also covers "the user typed
rem something other than yes at the prompt" and "--dry-run". Deleting the venv
rem in either case would destroy the install of someone who just declined to
rem uninstall it.
if exist "%SENTINEL%" (
    echo.
    echo Removing the virtual environment...
    rmdir /S /Q "%VENV%"
    if exist "%VENV%" (
        echo   WARNING: %VENV% could not be fully removed.
        echo   Something still holds a file open in it. Close Claude Desktop
        echo   and any terminal using it, then delete the folder by hand.
        set "CODE=1"
    ) else (
        del /Q "%SENTINEL%" 2>nul
        echo   done: %VENV%
    )
)

echo.
pause
exit /b %CODE%
