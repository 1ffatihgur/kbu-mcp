# KBÜ MCP indeksini güncel tutar (depo kopyasından çalıştırılan kurulumlar için).
# Görev Zamanlayıcı ile periyodik çalıştırılır; kilit ve ilerleme kbu_mcp.updater içinde yönetilir.
#   -Mode hot   : ana sayfalar ve duyuru/haber listeleri + yeni çıkan linkler (saatlik)
#   -Mode daily : 24 saatten eski tüm sayfa/dosyalar + telefon rehberi + yeni taranmış PDF'lerin OCR'ı (gecelik)
param([ValidateSet("hot", "daily")] [string]$Mode = "daily")

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$log = Join-Path $root "data\refresh.log"
$env:PYTHONIOENCODING = "utf-8"

Add-Content -Encoding UTF8 $log "$(Get-Date -Format s) [$Mode] başladı"
if ($Mode -eq "hot") {
    & $py -m kbu_mcp update --hot 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
} else {
    & $py -m kbu_mcp update 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
}
Add-Content -Encoding UTF8 $log "$(Get-Date -Format s) [$Mode] bitti (çıkış kodu $LASTEXITCODE)"
