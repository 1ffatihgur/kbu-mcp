@echo off
rem Çift tıklayın: Docker, veritabanı ve Ollama'yı açar, uzak MCP adresini yazar. Kapatmak için pencereyi kapatın.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\baslat.ps1"
pause
