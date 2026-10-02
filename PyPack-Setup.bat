@echo off
setlocal EnableExtensions DisableDelayedExpansion
title PyPack
if not exist "%~dp0pypack_setup.py" (
  echo [PyPack] Missing pypack_setup.py. Keep the application beside this launcher.
  exit /b 1
)
set "PYCMD="
set "PYARGS="
call :find_python
if defined PYCMD goto :launch
if not exist "%~dp0pypack-bootstrap.ps1" (
  echo [PyPack] Missing pypack-bootstrap.ps1. Install Python 3.10+ manually.
  exit /b 1
)
echo [PyPack] Python 3.10+ not found. Installing Python...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0pypack-bootstrap.ps1"
if errorlevel 1 exit /b 1
call :refresh_path
call :find_python
if not defined PYCMD (
  echo [PyPack] Python still unavailable. Restart your terminal or install Python manually.
  exit /b 1
)
:launch
echo [PyPack] Starting with "%PYCMD%" %PYARGS%
if "%~1"=="" (
  "%PYCMD%" %PYARGS% "%~dp0pypack_setup.py" --gui
) else (
  "%PYCMD%" %PYARGS% "%~dp0pypack_setup.py" %*
)
set "RC=%errorlevel%"
if "%~1"=="" if not "%PYPACK_NO_PAUSE%"=="1" pause
exit /b %RC%

:try_python
"%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "PYCMD=%~1"
set "PYARGS="
exit /b 0

:find_python
set "PYCMD="
set "PYARGS="
if defined VIRTUAL_ENV call :try_python "%VIRTUAL_ENV%\Scripts\python.exe"
if defined PYCMD exit /b 0
call :try_python python
if defined PYCMD exit /b 0
call :try_python python3
if defined PYCMD exit /b 0
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :known_paths
set "PYCMD=py"
set "PYARGS=-3"
exit /b 0
:known_paths
for %%D in (Python316 Python315 Python314 Python313 Python312 Python311 Python310) do (
  if not defined PYCMD call :try_python "%LocalAppData%\Programs\Python\%%D\python.exe"
  if not defined PYCMD call :try_python "%ProgramFiles%\%%D\python.exe"
  if not defined PYCMD call :try_python "%ProgramFiles(x86)%\%%D\python.exe"
)
exit /b 0

:refresh_path
for /f "delims=" %%P in ('powershell.exe -NoProfile -Command "[Environment]::ExpandEnvironmentVariables([Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User'))"') do set "PATH=%PATH%;%%P"
exit /b 0
