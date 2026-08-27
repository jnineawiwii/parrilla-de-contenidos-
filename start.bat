@echo off
title RTP CMS - Sistema de Gestión de Contenidos
color 0A
cls
echo =====================================================
echo    🚀 RTP CMS - Sistema de Gestión de Contenidos
echo =====================================================
echo.
echo    Iniciando servidor...
echo.
echo    📡 Acceso local: http://localhost:5000
echo    📱 Acceso en red: http://10.106.0.125:5000
echo.
echo    Presiona Ctrl+C para detener el servidor
echo =====================================================
echo.

cd /d "%~dp0"

REM Ejecutar la aplicación
python app.py

pause