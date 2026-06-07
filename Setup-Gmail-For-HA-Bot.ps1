$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = "C:\Users\Felix\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$gmailScript = Join-Path $projectDir "gmail_ha_actions.py"
$credentials = Join-Path $projectDir "gmail_credentials.json"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Cannot find Python runtime:" -ForegroundColor Red
    Write-Host $pythonExe
    exit 1
}

if (-not (Test-Path $gmailScript)) {
    Write-Host "Cannot find Gmail setup script:" -ForegroundColor Red
    Write-Host $gmailScript
    exit 1
}

if (-not (Test-Path $credentials)) {
    Write-Host "Missing Gmail OAuth file:" -ForegroundColor Yellow
    Write-Host $credentials
    Write-Host ""
    Write-Host "Download the Google OAuth desktop client JSON and rename it to gmail_credentials.json."
    Write-Host "Put it in this folder:"
    Write-Host $projectDir
    exit 1
}

Write-Host ""
Write-Host "Starting Gmail authorization..." -ForegroundColor Green
Write-Host "A browser window will open. Sign in to Gmail and allow read-only access."
Write-Host ""

& $pythonExe $gmailScript 20260428
