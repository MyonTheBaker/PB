@echo off
setlocal
cd /d "%~dp0.."
.venv\Scripts\python.exe whatsapp_order_exporter\receiver.py
if errorlevel 1 pause
