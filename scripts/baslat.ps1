# KBÜ MCP'yi tek komutla başlatır: Docker + PostgreSQL + Ollama kontrolü, ardından uzak MCP adresi (Cloudflare tüneli).
#   powershell -ExecutionPolicy Bypass -File scripts\baslat.ps1
# Durdurmak için bu pencerede Ctrl+C.
$here = $PSScriptRoot
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here "demo.ps1")
Write-Host ""
Write-Host "Uzak MCP adresi açılıyor..." -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here "remote.ps1")
