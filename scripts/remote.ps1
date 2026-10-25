# KBÜ MCP'yi kendi bilgisayarınızdan internete açar (ücretsiz, hesap gerektirmez):
#   1) MCP sunucusunu HTTP modunda başlatır (http://127.0.0.1:7860)
#   2) Cloudflare quick tunnel açar ve https://<rastgele>.trycloudflare.com/mcp adresini yazar
# Gereken: cloudflared  (winget install Cloudflare.cloudflared)
# Durdurmak için bu pencerede Ctrl+C. Adres her başlatmada değişir; tünel düşerse (uyku, ağ kesintisi)
# kendiliğinden yeniden açılır ve yeni adres ekrana yazılır.
param([int]$Port = 7860)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$logDir = Join-Path $root "data"; New-Item -ItemType Directory -Force $logDir | Out-Null
$srvLog = Join-Path $logDir "remote_server.log"; $cfLog = Join-Path $logDir "tunnel.log"
$urlFile = Join-Path $logDir "remote_url.txt"

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

# Önceki çalıştırmadan kalan süreçleri temizle (bilgisayar uyku/hızlı başlatmadan dönünce açık kalabiliyorlar)
Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" | Where-Object { $_.CommandLine -match "127\.0\.0\.1:$Port" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
Remove-Item $cfLog, "$cfLog.out", $urlFile -ErrorAction SilentlyContinue

function Start-Tunnel {
    Remove-Item $cfLog, "$cfLog.out" -ErrorAction SilentlyContinue
    $p = Start-Process $cf -ArgumentList @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Port") -PassThru -WindowStyle Hidden -RedirectStandardError $cfLog -RedirectStandardOutput "$cfLog.out"
    $u = $null
    for ($i = 0; $i -lt 60 -and -not $u -and -not $p.HasExited; $i++) {
        Start-Sleep 1
        $m = Select-String -Path $cfLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { $u = $m.Matches[0].Value }
    }
    if (-not $u) { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }; return $null }
    # Adres yayılana kadar bekle, sonra dışarıdan erişilebildiğini doğrula
    for ($i = 0; $i -lt 30; $i++) {
        try { Invoke-WebRequest "$u/health" -UseBasicParsing -TimeoutSec 5 | Out-Null; break } catch { Start-Sleep 2 }
    }
    "$u/mcp" | Set-Content -Encoding UTF8 $urlFile
    Write-Host ""
    Write-Host ("[{0:HH:mm}] Remote MCP adresi:  $u/mcp" -f (Get-Date)) -ForegroundColor Green
    Write-Host "         Tanıtım sayfası:    $u/"
    Write-Host "         Claude Desktop / claude.ai: Settings > Connectors > Add custom connector > URL: $u/mcp"
    Write-Host "         Claude Code: claude mcp add --transport http karabuk-universitesi $u/mcp"
    return @{ Proc = $p; Url = $u }
}

function Test-Tunnel($t) {
    if ($t.Proc.HasExited) { return $false }
    if (Select-String -Path $cfLog -Pattern "Tunnel not found|failed to connect" -ErrorAction SilentlyContinue | Select-Object -Last 1) {
        try { Invoke-WebRequest "$($t.Url)/health" -UseBasicParsing -TimeoutSec 10 | Out-Null; return $true } catch { return $false }
    }
    return $true
}

Write-Host "MCP sunucusu başlatılıyor..."
$srv = Start-Process $srvExe -ArgumentList $srvArgs -PassThru -WindowStyle Hidden -RedirectStandardOutput $srvLog -RedirectStandardError "$srvLog.err"
$tun = $null
try {
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-WebRequest "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 3 | Out-Null; break } catch { Start-Sleep 2 }
    }
    Write-Host "Tünel açılıyor..."
    $tun = Start-Tunnel
    if (-not $tun) { throw "Tünel açılamadı; ayrıntı: $cfLog" }
    Write-Host ""
    Write-Host "Durdurmak için Ctrl+C. (Tünel düşerse kendiliğinden yeniden açılır; adres değişir.)"
    $lastCheck = Get-Date
    while (-not $srv.HasExited) {
        Start-Sleep 5
        if (((Get-Date) - $lastCheck).TotalSeconds -ge 60) {
            $lastCheck = Get-Date
            if (-not (Test-Tunnel $tun)) {
                Write-Host ("[{0:HH:mm}] Tünel düştü, yeniden açılıyor..." -f (Get-Date)) -ForegroundColor Yellow
                if (-not $tun.Proc.HasExited) { Stop-Process -Id $tun.Proc.Id -Force -ErrorAction SilentlyContinue }
                $new = Start-Tunnel
                if ($new) { $tun = $new } else { Write-Host "Yeniden açılamadı, 1 dakika sonra tekrar denenecek." -ForegroundColor Red }
            }
        }
    }
    Write-Host "MCP sunucusu kapandı; ayrıntı: $srvLog.err" -ForegroundColor Red
} finally {
    foreach ($p in @($tun.Proc, $srv)) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }
    Remove-Item $urlFile -ErrorAction SilentlyContinue
}
