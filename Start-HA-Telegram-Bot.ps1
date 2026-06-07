$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = "C:\Users\Felix\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$botScript = Join-Path $projectDir "telegram_ha_bot.py"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Cannot find Python runtime:" -ForegroundColor Red
    Write-Host $pythonExe
    exit 1
}

if (-not (Test-Path $botScript)) {
    Write-Host "Cannot find Telegram bot script:" -ForegroundColor Red
    Write-Host $botScript
    exit 1
}

if (-not $env:TELEGRAM_BOT_TOKEN) {
    $token = Read-Host "Paste Telegram Bot token from BotFather"
    if (-not $token) {
        Write-Host "No token entered. Bot not started." -ForegroundColor Yellow
        exit 1
    }
    $env:TELEGRAM_BOT_TOKEN = $token
}

$env:HA_BOT_ALLOW_SELF_SIGNED_SSL = "1"

Write-Host ""
Write-Host "Starting HA Telegram Bot..." -ForegroundColor Green
Write-Host "Using self-signed SSL mode for Telegram connection." -ForegroundColor Yellow
Write-Host "Keep this PowerShell window open while you send photos in Telegram."
Write-Host "Press Ctrl+C to stop."
Write-Host ""

& $pythonExe $botScript
