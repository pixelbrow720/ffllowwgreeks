$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$here = "c:\FLOWGREEKS\backend\scripts\validation"
$backend = "c:\FLOWGREEKS\backend"
$python = "$here\.venv\Scripts\python.exe"
$dumper = "$backend\bin\dump_fg_greeks.exe"

$runs = @(
  @{date="2026-02-02"; root="NDX"},
  @{date="2026-02-03"; root="SPX"},
  @{date="2026-02-03"; root="NDX"},
  @{date="2026-02-04"; root="SPX"},
  @{date="2026-02-04"; root="NDX"}
)
$snap = "16:00:00Z"

foreach ($r in $runs) {
  $date = $r.date
  $root = $r.root
  Write-Host "=== $date $root $snap ===" -ForegroundColor Cyan
  Push-Location $here
  & $python iv_parity.py $date --snapshot $snap --root $root | Out-Host
  $refCsv = "$here\outputs\$date\iv_ref_${root}_160000Z.csv"
  $fgCsv = "$here\outputs\$date\iv_fg_${root}_160000Z.csv"
  $metaJson = "$here\outputs\$date\iv_ref_${root}_160000Z.json"
  $meta = Get-Content $metaJson | ConvertFrom-Json
  Pop-Location
  Push-Location $backend
  & $dumper -in $refCsv -out $fgCsv -spot $meta.spot -r $meta.rfr -q $meta.div | Out-Host
  Pop-Location
  Push-Location $here
  & $python iv_diff.py $date --root $root --snapshot $snap | Out-Host
  Pop-Location
}
