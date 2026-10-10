# Copies the data that cannot be rebuilt to a backup folder:
#   data\landing  raw source files (forecast captures cannot be downloaded again)
#   data\live     live and replay predictions with their verified outcomes
#   models        trained model files
# Marts are left out: they are rebuilt from these by the pipeline.
#
# New and changed files are copied; nothing is deleted from the backup. A SHA-256 list of the
# source is written next to the copy, so a restore can be checked with -Verify.
#
#   .\backup_landing.ps1                                   # back up to D:\backupdata\plovdiv
#   .\backup_landing.ps1 -Destination E:\plovdiv -Verify   # back up, then compare every file
param(
    [string]$Destination = "D:\backupdata\plovdiv",
    [switch]$Verify
)

$ErrorActionPreference = "Stop"
$parts = @{
    "landing" = Join-Path $PSScriptRoot "data\landing"
    "live"    = Join-Path $PSScriptRoot "data\live"
    "models"  = Join-Path $PSScriptRoot "models"
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$manifest = @()
foreach ($name in $parts.Keys) {
    $source = $parts[$name]
    if (-not (Test-Path $source)) { continue }
    $target = Join-Path $Destination $name
    # /E subfolders, /XO keep a newer copy, /R /W retry briefly on locked files. Exit codes below 8 are success.
    robocopy $source $target /E /XO /R:2 /W:5 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $name with exit code $LASTEXITCODE" }
    Get-ChildItem $source -Recurse -File | ForEach-Object {
        $manifest += [pscustomobject]@{
            Path   = "$name\" + $_.FullName.Substring($source.Length + 1)
            Sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
        }
    }
}
$manifest | Export-Csv (Join-Path $Destination "sha256.csv") -NoTypeInformation -Encoding UTF8
Write-Output "backed up $($manifest.Count) files to $Destination"

if ($Verify) {
    $bad = 0
    foreach ($entry in $manifest) {
        $copy = Join-Path $Destination $entry.Path
        if (-not (Test-Path $copy) -or (Get-FileHash $copy -Algorithm SHA256).Hash -ne $entry.Sha256) {
            Write-Output "MISMATCH $($entry.Path)"
            $bad++
        }
    }
    if ($bad) { throw "$bad files differ from the source" }
    Write-Output "verified: every file matches its SHA-256"
}
exit 0
