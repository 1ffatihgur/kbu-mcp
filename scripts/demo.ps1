# Sunum / demo öncesi hazırlık: Docker + PostgreSQL + Ollama'yı açar ve MCP'nin çalıştığını doğrular.
#   powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Ok($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Bad($m) { Write-Host "  [!!] $m" -ForegroundColor Red }

Write-Host "1) Docker"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    for ($i = 0; $i -lt 60; $i++) { Start-Sleep 3; docker info *> $null; if ($LASTEXITCODE -eq 0) { break } }
}
if ($LASTEXITCODE -eq 0) { Ok "Docker açık" } else { Bad "Docker açılmadı (Docker Desktop'ı elle açın)"; exit 1 }

Write-Host "2) Veritabanı"
docker compose up -d db *> $null
for ($i = 0; $i -lt 40; $i++) {
    docker compose exec -T db pg_isready -U kbu -d kbu *> $null
    if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 2
}
if ($LASTEXITCODE -eq 0) { Ok "PostgreSQL hazır" } else { Bad "PostgreSQL açılmadı: docker compose logs db" }

Write-Host "3) Anlamsal arama (Ollama + bge-m3)"
try { Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 3 | Out-Null } catch { Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep 5 }
try {
    $r = Invoke-RestMethod http://127.0.0.1:11434/v1/embeddings -Method Post -ContentType "application/json" -Body '{"model":"bge-m3","input":["test"]}' -TimeoutSec 60
    Ok "bge-m3 çalışıyor"
} catch { Bad "Ollama/bge-m3 yanıt vermiyor; arama yalnızca anahtar kelimeyle çalışır" }

Write-Host "4) Arama testi"
$env:PYTHONIOENCODING = "utf-8"
& "$root\.venv\Scripts\kbu.exe" search "yemekhane ücretleri" --limit 1 2>$null | Select-String "###" | ForEach-Object { Ok $_.Line.Trim() }

Write-Host "5) Karabük sitesi ve Claude erişimi"
try { Invoke-WebRequest https://www.karabuk.edu.tr -UseBasicParsing -TimeoutSec 10 | Out-Null; Ok "karabuk.edu.tr erişilebilir (canlı araçlar)" } catch { Bad "karabuk.edu.tr'ye erişilemiyor (rehber, yemek listesi gibi canlı araçlar çalışmaz)" }
try { Invoke-WebRequest https://api.anthropic.com -UseBasicParsing -TimeoutSec 10 | Out-Null; Ok "Claude erişilebilir" } catch {
    if ($_.Exception.Response) { Ok "Claude erişilebilir" } else { Bad "api.anthropic.com engelli olabilir: telefondan internet paylaşımı (hotspot) kullanın" }
}
Write-Host ""
Write-Host "Hazır. Yeni bir terminalde:  claude    → sorularınızı yazın ('MCP kullan' demenize gerek yok)" -ForegroundColor Cyan
