# PowerShell task runner (alternative to `just`/`make`). Usage: ./scripts/dev.ps1 <target>
param([Parameter(Position = 0)][string]$Target = "help")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "backend/.venv/Scripts/python.exe"

function Invoke-Checked([scriptblock]$Cmd) {
    & $Cmd
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
}

function Install-Backend {
    if (-not (Test-Path $py)) { python -m venv (Join-Path $root "backend/.venv") }
    Invoke-Checked { & $py -m pip install -q -e "$root/backend[dev]" }
}

switch ($Target) {
    "setup" { Install-Backend; Push-Location "$root/frontend"; try { Invoke-Checked { npm install } } finally { Pop-Location } }
    "dev" { Invoke-Checked { docker compose -f "$root/docker-compose.yml" up --build } }
    "test" {
        Push-Location "$root/backend"
        try { Invoke-Checked { & $py -m pytest --cov=schemashift } } finally { Pop-Location }
        Push-Location "$root/frontend"
        try { Invoke-Checked { npm test } } finally { Pop-Location }
    }
    "test-integration" {
        Invoke-Checked { docker compose -f "$root/docker-compose.test.yml" up -d }
        Push-Location "$root/backend"
        try { Invoke-Checked { & $py -m pytest -m integration } } finally { Pop-Location }
    }
    "lint" {
        Push-Location "$root/backend"
        try { Invoke-Checked { & $py -m ruff check . }; Invoke-Checked { & $py -m ruff format --check . } } finally { Pop-Location }
        Push-Location "$root/frontend"
        try { Invoke-Checked { npm run lint } } finally { Pop-Location }
    }
    "typecheck" {
        Push-Location "$root/backend"
        try { Invoke-Checked { & $py -m mypy schemashift } } finally { Pop-Location }
        Push-Location "$root/frontend"
        try { Invoke-Checked { npm run typecheck } } finally { Pop-Location }
    }
    "evaluate" { Invoke-Checked { & $py -m schemashift.cli evaluate } }
    "migrate" { Push-Location "$root/backend"; try { Invoke-Checked { & $py -m alembic upgrade head } } finally { Pop-Location } }
    "seed-samples" { Write-Host "Not implemented until samples exist (Phase 6+)." }
    "build" {
        Invoke-Checked { docker compose -f "$root/docker-compose.yml" build }
        Push-Location "$root/frontend"; try { Invoke-Checked { npm run build } } finally { Pop-Location }
    }
    default { Write-Host "Targets: setup dev test test-integration lint typecheck evaluate migrate seed-samples build" }
}
