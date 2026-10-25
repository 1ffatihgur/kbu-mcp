# KBÜ MCP'yi kendi bilgisayarınızdan internete açar (ücretsiz, hesap gerektirmez):
#   1) MCP sunucusunu HTTP modunda başlatır (http://127.0.0.1:7860)
#   2) Cloudflare quick tunnel açar ve https://<rastgele>.trycloudflare.com/mcp adresini yazar
# Gereken: cloudflared  (winget install Cloudflare.cloudflared)
# Durdurmak için bu pencerede Ctrl+C. Adres her başlatmada değişir.
param([int]$Port = 7860)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

# MCP sunucusu: depo kopyasında .venv, değilse uvx ile GitHub'dan
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $srvExe = $venvPy; $srvArgs = @("-m", "kbu_mcp.server", "--http", "--port", "$Port")
} else {
    $srvExe = "uvx"; $srvArgs = @("--from", "git+https://github.com/1ffatihgur/kbu-mcp", "kbu-mcp", "--http", "--port", "$Port")
}

$cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cf) { $cf = @("C:\Program Files (x86)\cloudflared\cloudflared.exe", "C:\Program Files\cloudflared\cloudflared.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1 }
if (-not $cf) { throw "cloudflared bulunamadı. Kurulum: winget install Cloudflare.cloudflared" }

$logDir = Join-Path $root "data"; New-Item -ItemType Directory -Force $logDir | Out-Null
$srvLog = Join-Path $logDir "remote_server.log"; $cfLog = Join-Path $logDir "tunnel.log"

Write-Host "MCP sunucusu başlatılıyor..."
$srv = Start-Process $srvExe -ArgumentList $srvArgs -PassThru -WindowStyle Hidden -RedirectStandardOutput $srvLog -RedirectStandardError "$srvLog.err"
try {
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-WebRequest "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 3 | Out-Null; break } catch { Start-Sleep 2 }
    }
    Write-Host "Tünel açılıyor..."
    $tun = Start-Process $cf -ArgumentList @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Port") -PassThru -WindowStyle Hidden -RedirectStandardError $cfLog -RedirectStandardOutput "$cfLog.out"
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep 1
        $m = Select-String -Path $cfLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value }
    }
    if (-not $url) { throw "Tünel adresi alınamadı; ayrıntı: $cfLog" }
    "$url/mcp" | Set-Content -Encoding UTF8 (Join-Path $logDir "remote_url.txt")
    Write-Host ""
    Write-Host "Remote MCP adresi:  $url/mcp" -ForegroundColor Green
    Write-Host "Tanıtım sayfası:    $url/"
    Write-Host ""
    Write-Host "Claude Desktop / claude.ai: Settings > Connectors > Add custom connector > URL: $url/mcp"
    Write-Host "Claude Code: claude mcp add --transport http karabuk-universitesi $url/mcp"
    Write-Host ""
    Write-Host "Durdurmak için Ctrl+C."
    while (-not $srv.HasExited -and -not $tun.HasExited) { Start-Sleep 2 }
    if ($srv.HasExited) { Write-Host "MCP sunucusu kapandı; ayrıntı: $srvLog.err" -ForegroundColor Red }
    if ($tun.HasExited) { Write-Host "Tünel kapandı; ayrıntı: $cfLog" -ForegroundColor Red }
} finally {
    foreach ($p in @($tun, $srv)) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }
    Remove-Item (Join-Path $logDir "remote_url.txt") -ErrorAction SilentlyContinue
}
