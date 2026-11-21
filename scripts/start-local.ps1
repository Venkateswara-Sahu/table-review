$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Push-Location $RepoRoot
try {
    if (Test-Path $VenvPython) {
        & $VenvPython (Join-Path $RepoRoot "scripts\start_local.py") @args
    } else {
        & uv run python (Join-Path $RepoRoot "scripts\start_local.py") @args
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
