@echo off
setlocal

chcp 65001 >nul

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-HA-Local-Report.ps1" -Date "%~1" -OutputDir "%~2"
