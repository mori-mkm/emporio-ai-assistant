# Optional convenience wrapper; Python entry point also works without PS script execution changes.
param([ValidateSet('harness','app')][string]$Mode = 'harness')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    & python (Join-Path $projectRoot 'scripts/verify.py') --mode $Mode
    $checkExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $checkExitCode
