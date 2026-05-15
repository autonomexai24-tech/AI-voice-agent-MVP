param(
  [int]$ApiPort = 8000,
  [int]$FrontendPort = 3000,
  [switch]$WithWorker
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root "frontend"
$Src = Join-Path $Root "src"

function Import-LocalEnv {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  Get-Content -LiteralPath $Path | ForEach-Object {
    if ($_ -match "^\s*#" -or $_ -notmatch "^\s*([^=]+)=(.*)$") {
      return
    }

    $name = $matches[1].Trim()
    $value = $matches[2].Trim().Trim('"').Trim("'")
    if ($name -and -not [Environment]::GetEnvironmentVariable($name, "Process")) {
      [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
  }
}

Import-LocalEnv (Join-Path $Root ".env")
Import-LocalEnv (Join-Path $Frontend ".env.local")

if ($env:PYTHONPATH) {
  $env:PYTHONPATH = "$Src;$env:PYTHONPATH"
} else {
  $env:PYTHONPATH = $Src
}

if (-not (Test-Path -LiteralPath (Join-Path $Frontend "node_modules"))) {
  throw "Frontend dependencies are missing. Run 'npm install' from the frontend directory first."
}

if (-not $env:DATABASE_URL -and $env:PERSISTENCE_ENABLED -ne "false") {
  Write-Warning "DATABASE_URL is not set. Backend APIs will return database_unavailable until PostgreSQL is configured."
} elseif (Get-Command pg_isready -ErrorAction SilentlyContinue) {
  pg_isready -d $env:DATABASE_URL | Out-Host
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "PostgreSQL did not report ready for DATABASE_URL."
  }
} else {
  Write-Warning "pg_isready was not found. Skipping PostgreSQL readiness check."
}

if ($env:DATABASE_URL -and $env:PERSISTENCE_ENABLED -ne "false") {
  python (Join-Path $Root "scripts\initialize_local_db.py") | Out-Host
}

$apiJob = Start-Job -Name "ai-voice-api" -ScriptBlock {
  param($RootPath, $SrcPath, $Port)
  Set-Location $RootPath
  $env:PYTHONPATH = $SrcPath
  python -m uvicorn api.main:app --host 127.0.0.1 --port $Port --reload
} -ArgumentList $Root, $Src, $ApiPort

$frontendJob = Start-Job -Name "ai-voice-frontend" -ScriptBlock {
  param($FrontendPath, $Port)
  Set-Location $FrontendPath
  npm run dev -- --hostname 127.0.0.1 --port $Port
} -ArgumentList $Frontend, $FrontendPort

$workerJob = $null
if ($WithWorker) {
  $workerJob = Start-Job -Name "ai-voice-worker" -ScriptBlock {
    param($RootPath, $SrcPath)
    Set-Location $RootPath
    $env:PYTHONPATH = $SrcPath
    python -m voice_agent.worker dev
  } -ArgumentList $Root, $Src
}

Write-Host "Local stack starting:"
Write-Host "  FastAPI:  http://127.0.0.1:$ApiPort"
Write-Host "  Frontend: http://127.0.0.1:$FrontendPort"
Write-Host "  API job:  $($apiJob.Id) ($($apiJob.Name))"
Write-Host "  UI job:   $($frontendJob.Id) ($($frontendJob.Name))"
if ($workerJob) {
  Write-Host "  Worker:   $($workerJob.Id) ($($workerJob.Name))"
} else {
  Write-Host "  Worker:   skipped; pass -WithWorker to start the realtime runtime worker"
}
Write-Host ""
Write-Host "Use Receive-Job -Name ai-voice-api -Keep or Receive-Job -Name ai-voice-frontend -Keep to inspect logs."
Write-Host "Use Stop-Job -Name ai-voice-api, ai-voice-frontend, ai-voice-worker to stop services."
