param(
    [string]$Symbol = 'spx',
    [string]$Start  = '2026-02-12T19:00:00Z',
    [string]$End    = '2026-02-12T19:01:00Z',
    [double]$Speed  = 0
)
$ErrorActionPreference = 'Stop'
Get-Content C:\FLOWGREEKS\backend\.env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+?)\s*=\s*(.*)$') {
        $name = $matches[1].Trim()
        $value = $matches[2].Trim()
        if ($value) { [Environment]::SetEnvironmentVariable($name, $value) }
    }
}
& C:\FLOWGREEKS\tmp\replay.exe --symbol $Symbol --start $Start --end $End --speed $Speed
