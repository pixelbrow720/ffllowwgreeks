$ErrorActionPreference = 'Stop'
Get-Content C:\FLOWGREEKS\backend\.env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+?)\s*=\s*(.*)$') {
        $name = $matches[1].Trim()
        $value = $matches[2].Trim()
        if ($value) { [Environment]::SetEnvironmentVariable($name, $value) }
    }
}
& C:\FLOWGREEKS\tmp\compute.exe
