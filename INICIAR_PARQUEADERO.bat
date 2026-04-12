@echo off
title Parqueadero El Puente
color 0A
echo.
echo  ========================================
echo    PARQUEADERO EL PUENTE - Iniciando...
echo  ========================================
echo.

REM Instalar Flask si no está instalado
pip show flask >nul 2>&1
if errorlevel 1 (
    echo  Instalando Flask por primera vez...
    pip install flask
)

echo  Sistema iniciando...
echo.
echo  Abre tu navegador en: http://localhost:5000
echo  Operador desde celular: busca la IP en la pantalla
echo.

REM Abrir navegador automáticamente después de 2 segundos
timeout /t 2 /nobreak >nul
start http://localhost:5000

REM Iniciar el servidor
python app.py

pause
