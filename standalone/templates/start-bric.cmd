@echo off
rem Start the BuildStock Rate Impact Calculator (stand-alone package).
rem
rem The first run of each version unpacks the bundled Python environment into
rem %LOCALAPPDATA%\BRIC\<version> (short path, outside OneDrive-synced
rem folders); later runs start straight away. Environments of older versions
rem are removed after a successful unpack. User data is never stored there.
setlocal
set "BRIC_VERSION=@BRIC_VERSION@"
set "HERE=%~dp0"
title BuildStock Rate Impact Calculator %BRIC_VERSION%

if not defined LOCALAPPDATA (
    echo LOCALAPPDATA is not set, so there is nowhere to install the app.
    goto fail
)
set "BRIC_ROOT=%LOCALAPPDATA%\BRIC"
set "INSTALL=%BRIC_ROOT%\%BRIC_VERSION%"
rem Written last by a successful unpack. A folder without it is left over from
rem an interrupted unpack and is redone.
set "MARKER=%INSTALL%\.bric-unpacked"

if exist "%MARKER%" goto run

if not exist "%HERE%environment.tar" (
    echo environment.tar was not found next to this script.
    echo Extract the whole zip file first: right-click it and choose "Extract All...".
    goto fail
)
echo Setting up BuildStock Rate Impact Calculator %BRIC_VERSION%.
echo This happens once per version and takes a minute or two...
echo.
if exist "%INSTALL%" rmdir /s /q "%INSTALL%"
"%HERE%pixi-unpack.exe" "%HERE%environment.tar" --output-directory "%INSTALL%" --shell cmd
if errorlevel 1 goto fail
echo %BRIC_VERSION%> "%MARKER%"

rem Remove environments left by older versions (about 2 GB each).
for /d %%D in ("%BRIC_ROOT%\*") do if /i not "%%~nxD"=="%BRIC_VERSION%" rmdir /s /q "%%D"
echo.

:run
call "%INSTALL%\activate.bat"
rem Arguments are passed through (e.g. --no-browser --port 8599 for automated checks).
bric %*
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
echo BuildStock Rate Impact Calculator could not start. See the messages above.
pause
exit /b 1
