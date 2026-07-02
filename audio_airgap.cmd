@echo off
setlocal
python "%~dp0audio_airgap.py" %*
exit /b %ERRORLEVEL%
