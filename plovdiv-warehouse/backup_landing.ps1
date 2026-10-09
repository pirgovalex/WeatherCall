
$source = Join-Path $PSScriptRoot "data\landing"
$dest = "D:\backupdata\plovdiv_landing"
if (!(Test-Path $dest)) { New-Item -ItemType Directory -Force -Path $dest | Out-Null }
if (Test-Path $source) { Copy-Item -Path $source\* -Destination $dest -Recurse -Force }

