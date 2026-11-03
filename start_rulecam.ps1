#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Local UI-development launcher for Rulecam with SAMPLE data (FastAPI + Next.js cockpit).
    Not a production deploy (see docs/deploy.md) and not a live run: never record a demo
    video or quote a number from what it shows.
.DESCRIPTION
    1. Seeds hand-made SAMPLE data (`scripts/seed_live_backend.py`): stock clips and stills,
       two sample SitePlans and a sample evidence chain whose verdicts and outcomes were
       written by hand (every record is labelled "[SAMPLE ...]"; see samples/seed/README.md).
    2. Starts the FastAPI backend on http://localhost:8000 with RULECAM_RUNTIME=true.
    3. Starts the Next.js operational cockpit on http://localhost:3000.
    4. Verifies live health and registers all plans in runtime memory.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$env:PYTHONIOENCODING = "utf-8"
$env:RULECAM_RUNTIME = "true"

Write-Host "==> [1/4] Seeding the local backend with hand-made SAMPLE data (not a live run)..." -ForegroundColor Cyan
uv run python scripts/seed_live_backend.py

# Check if FastAPI is already listening on port 8000
$apiUp = $false
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 8
    if ($null -ne $resp.mode) { $apiUp = $true }
} catch {}

if (-not $apiUp) {
    Write-Host "==> [2/4] Starting FastAPI backend on http://localhost:8000 (RULECAM_RUNTIME=true)..." -ForegroundColor Cyan
    Start-Process -FilePath "uv" -ArgumentList "run", "uvicorn", "rulecam.api.app:app", "--host", "0.0.0.0", "--port", "8000" -WorkingDirectory $RepoRoot -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 8
            if ($null -ne $resp.mode) { $apiUp = $true; break }
        } catch {}
    }
} else {
    Write-Host "==> [2/4] FastAPI backend already running on http://localhost:8000." -ForegroundColor Green
    # Ensure in-memory PlanStore has all 3 plans registered
    uv run python -c "import httpx, yaml; from pathlib import Path; [httpx.post('http://localhost:8000/plans', json=yaml.safe_load(p.read_text(encoding='utf-8')), timeout=4.0) for p in sorted(Path('plans').glob('*.plan.yaml'))]; httpx.post('http://localhost:8000/plans/plan-c914e72a03f5/approve', timeout=4.0)" 2>$null
}

# Check if Next.js UI is already listening on port 3000
$uiUp = $false
try {
    $uiResp = Invoke-WebRequest -Uri "http://localhost:3000/monitor" -UseBasicParsing -TimeoutSec 3
    if ($uiResp.StatusCode -eq 200) { $uiUp = $true }
} catch {}

if (-not $uiUp) {
    Write-Host "==> [3/4] Starting Next.js Operational Cockpit on http://localhost:3000..." -ForegroundColor Cyan
    Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" -WorkingDirectory (Join-Path $RepoRoot "ui") -WindowStyle Hidden
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $uiResp = Invoke-WebRequest -Uri "http://localhost:3000/monitor" -UseBasicParsing -TimeoutSec 2
            if ($uiResp.StatusCode -eq 200) { $uiUp = $true; break }
        } catch {}
    }
} else {
    Write-Host "==> [3/4] Next.js Operational Cockpit already running on http://localhost:3000." -ForegroundColor Green
}

Write-Host "==> [4/4] Local cockpit ready, running on SAMPLE data (not a live run)." -ForegroundColor Green
Write-Host "    - Cockpit      : http://localhost:3000/monitor" -ForegroundColor Yellow
Write-Host "    - Camera Fleet : http://localhost:3000/streams" -ForegroundColor Yellow
Write-Host "    - Rule Planner : http://localhost:3000/planner" -ForegroundColor Yellow
Write-Host "    - Merkle Audit : http://localhost:3000/audit" -ForegroundColor Yellow
Write-Host "    - System & GPU : http://localhost:3000/system" -ForegroundColor Yellow
Write-Host "    - Backend API  : http://localhost:8000/health" -ForegroundColor Yellow
