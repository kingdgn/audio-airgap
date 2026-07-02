@echo off
setlocal
python "%~dp0audio_web.py" --host 127.0.0.1 --port 8765
exit /b %ERRORLEVEL%
