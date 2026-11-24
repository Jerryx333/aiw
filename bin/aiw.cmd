@echo off
setlocal
if not defined AIW_PYTHON set "AIW_PYTHON=python"
"%AIW_PYTHON%" "%~dp0..\ai_worker.py" %*
exit /b %ERRORLEVEL%
