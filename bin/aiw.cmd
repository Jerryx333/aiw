@echo off
setlocal
if not defined AIW_PYTHON set "AIW_PYTHON=python"
rem aiw and the tools it runs read and write UTF-8; Windows would use the console code page.
set "PYTHONUTF8=1"
"%AIW_PYTHON%" "%~dp0..\aiw_windows.py" %*
exit /b %ERRORLEVEL%
