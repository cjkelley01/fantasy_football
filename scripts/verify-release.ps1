param(
    [switch]$IncludeWindowsProcessTests
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Release verification requires the setup-local.ps1 virtual environment at $python."
}

function Invoke-ReleaseStep {
    param([string]$Name, [string]$Command, [string[]]$Arguments)
    Write-Host "`n== $Name ==" -ForegroundColor Cyan
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

Push-Location $repositoryRoot
try {
    Invoke-ReleaseStep "Python credential-free suite" $python @("-m", "pytest")
    Invoke-ReleaseStep "Python compilation" $python @("-m", "compileall", "-q", "src", "tests")
    Invoke-ReleaseStep "Ruff correctness" $python @(
        "-m", "ruff", "check", "--select", "E9,F63,F7,F82", "."
    )
    Invoke-ReleaseStep "Python dependency consistency" $python @("-m", "pip", "check")
    Invoke-ReleaseStep "Frontend tests" "npm.cmd" @("--prefix", "frontend", "test")
    Invoke-ReleaseStep "TypeScript typecheck" "npm.cmd" @("--prefix", "frontend", "run", "typecheck")
    Invoke-ReleaseStep "Production frontend build" "npm.cmd" @("--prefix", "frontend", "run", "build")
    Invoke-ReleaseStep "Production dependency audit" "npm.cmd" @("--prefix", "frontend", "audit", "--omit=dev", "--audit-level=high")
    Invoke-ReleaseStep "Working-tree whitespace" "git.exe" @("diff", "--check", "HEAD")
    Invoke-ReleaseStep "Committed-revision whitespace" "git.exe" @(
        "show", "--check", "--format=", "HEAD"
    )

    if ($IncludeWindowsProcessTests) {
        Invoke-ReleaseStep "Disposable Windows process/recovery tests" $python @(
            "-m", "pytest", "tests/test_local_installation.py", "tests/test_m8_3_process_recovery.py"
        )
    }
    else {
        Write-Host "`nWindows process/recovery tier skipped. Re-run with -IncludeWindowsProcessTests." -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
}
