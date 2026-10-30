@echo off
rem Start the BuildStock Rate Impact Calculator (stand-alone package).
rem
rem The first run of each build unpacks the bundled Python environment into
rem %LOCALAPPDATA%\BRIC\<version> (short path, outside OneDrive-synced
rem folders); later runs start straight away. Environments of older versions
rem are removed after a successful unpack. User data is never stored there.
setlocal
set "BRIC_VERSION=@BRIC_VERSION@"
rem Identifies this exact build, so a rebuild of the same version re-unpacks.
set "BRIC_BUILD=@BRIC_BUILD@"
set "HERE=%~dp0"
title BuildStock Rate Impact Calculator %BRIC_VERSION%

if not defined LOCALAPPDATA (
    echo LOCALAPPDATA is not set, so there is nowhere to install the app.
    goto setup_failed
)
set "BRIC_ROOT=%LOCALAPPDATA%\BRIC"
set "INSTALL=%BRIC_ROOT%\%BRIC_VERSION%"
rem Must match default_log_dir() in launcher.py.
set "LOG_DIR=%BRIC_ROOT%\logs"
if defined BRIC_LOG_DIR set "LOG_DIR=%BRIC_LOG_DIR%"
rem Written last by a successful unpack and holds its build. A missing or
rem different build means an interrupted unpack or a new build: redo it.
set "MARKER=%INSTALL%\.bric-unpacked"

set "INSTALLED_BUILD="
if exist "%MARKER%" set /p INSTALLED_BUILD=<"%MARKER%"
if "%INSTALLED_BUILD%"=="%BRIC_BUILD%" goto run

if not exist "%HERE%environment.tar" (
    echo environment.tar was not found next to this script.
    echo Extract the whole zip file first: right-click it and choose "Extract All...".
    goto setup_failed
)
echo Setting up BuildStock Rate Impact Calculator %BRIC_VERSION%.
echo This happens once per version and takes a minute or two...
echo.
if exist "%INSTALL%" rmdir /s /q "%INSTALL%"
"%HERE%pixi-unpack.exe" "%HERE%environment.tar" --output-directory "%INSTALL%" --shell cmd
if errorlevel 1 goto setup_failed
rem Import libraries (.lib) and C headers are only used to compile against the
rem environment, never to run the app.
del /s /q "%INSTALL%\env\*.lib" >nul 2>&1
for %%I in ("%INSTALL%\env\include" "%INSTALL%\env\Library\include") do if exist %%I rmdir /s /q %%I
echo %BRIC_BUILD%> "%MARKER%"

rem Remove environments left by older versions (about 2 GB each).
for /d %%D in ("%BRIC_ROOT%\*") do if /i not "%%~nxD"=="%BRIC_VERSION%" if /i not "%%~nxD"=="logs" rmdir /s /q "%%D"
echo.

:run
call "%INSTALL%\activate.bat"
rem Arguments are passed through (e.g. --no-browser --port 8599 for automated checks).
bric %*
rem Anything but 0 is a failure. Crash codes such as 0xC0000005 are negative,
rem which "if errorlevel 1" would miss.
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0

echo.
echo BuildStock Rate Impact Calculator stopped unexpectedly (exit code %RC%).
echo The end of its log file (in %LOG_DIR%) follows:
echo.
powershell -NoProfile -Command "Get-ChildItem -LiteralPath $env:LOG_DIR -Filter 'bric-*.log' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1 | Get-Content -Tail 40"
echo.
echo If this keeps happening, please send that log file to the BRIC developers.
pause
exit /b %RC%

:setup_failed
echo.
echo BuildStock Rate Impact Calculator could not be set up. See the messages above.
pause
exit /b 1
