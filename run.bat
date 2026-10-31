@echo off
rem ===================================================================
rem  Pocket Mortys Bundle Catalog - double-click me
rem
rem  Installs what is needed the first time, builds the catalog if it is
rem  missing, then opens the browser.
rem ===================================================================
setlocal
cd /d "%~dp0"

set PY=py -3
%PY% --version >nul 2>&1 || set PY=python
%PY% --version >nul 2>&1 || (
  echo Python was not found.
  echo Install Python 3.9 or newer from https://www.python.org/downloads/
  echo and tick "Add Python to PATH" during setup.
  pause
  exit /b 1
)

echo Checking requirements...
%PY% -m pip install -q -r requirements.txt || (
  echo Could not install the requirements. The error above says why.
  pause
  exit /b 1
)

if not exist "AssetBundles" (
  echo.
  echo No AssetBundles folder here.
  echo Put your bundle files in a folder called AssetBundles next to this script,
  echo or start the server yourself:   python catalog_server.py "C:\path\to\bundles"
  pause
  exit /b 1
)

if not exist "catalog.json" (
  echo.
  echo Building the catalog. This takes a couple of minutes the first time.
  %PY% catalog_builder.py AssetBundles -o catalog.json || (
    echo Catalog build failed.
    pause
    exit /b 1
  )
)

echo.
%PY% catalog_server.py AssetBundles
pause
