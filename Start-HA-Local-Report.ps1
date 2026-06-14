param(
    [string]$Date,
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ifNotSetOutputDir = Join-Path $projectDir "outputs\Test"
if (-not $OutputDir) {
    $OutputDir = $ifNotSetOutputDir
}
$pythonExe = "C:\Users\Felix\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$scriptPath = Join-Path $projectDir "build_local_ha_report.py"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Cannot find Python runtime:" -ForegroundColor Red
    Write-Host $pythonExe
    exit 1
}

if (-not (Test-Path $scriptPath)) {
    Write-Host "Cannot find local report script:" -ForegroundColor Red
    Write-Host $scriptPath
    exit 1
}

if (-not $Date) {
    $Date = Read-Host "Enter report date in YYYYMMDD format"
}

if ($Date -notmatch '^\d{8}$') {
    Write-Host "Date must be YYYYMMDD." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Building local HA report..." -ForegroundColor Green
Write-Host "Date: $Date"
Write-Host "Photos: $(Join-Path $projectDir "Photo\$Date")"
Write-Host "Output: $OutputDir"
Write-Host ""

& $pythonExe $scriptPath $Date --output-dir $OutputDir
