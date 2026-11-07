[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 20,
    [switch]$PreserveOnTimeout,
    [string]$RuntimeDirectory = $(if ($env:WAR_ROOM_RUNTIME_DIRECTORY) { $env:WAR_ROOM_RUNTIME_DIRECTORY } else { "outputs\runtime" })
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "local-runtime.ps1")
if ([IO.Path]::IsPathRooted($RuntimeDirectory)) {
    $runtimePath = [IO.Path]::GetFullPath($RuntimeDirectory)
} else {
    $runtimePath = [IO.Path]::GetFullPath((Join-Path $repo $RuntimeDirectory))
}
$metadataPath = Join-Path $runtimePath "war-room-server.json"
if (-not (Test-Path $metadataPath)) { Write-Host "War Room is not running (no launcher metadata)."; return }
$metadata = Read-WarRoomMetadata $metadataPath
$process = Get-WarRoomOwnedProcess $metadata
if (-not $process) {
    Remove-Item -LiteralPath $metadataPath -Force
    Write-Warning "Removed stale launcher metadata; the recorded process was not running."
    return
}
$null = Assert-WarRoomProcessCommandIdentity $metadata $process
$ownsListener = Test-WarRoomPortOwner -Port ([int]$metadata.port) -ProcessId $process.Id
if ($ownsListener) {
    $null = Assert-WarRoomHealthIdentity -Port ([int]$metadata.port) -ExpectedRunId ([string]$metadata.run_id) -ExpectedProcessId $process.Id
    [string]$metadata.run_id | Set-Content -LiteralPath ([string]$metadata.shutdown_file) -Encoding ASCII
    Write-Host "Graceful shutdown requested for authenticated War Room PID $($process.Id)."
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
} else {
    Write-Warning "The authenticated War Room process no longer owns its listener; entering forced recovery without relying on HTTP health."
    $deadline = [DateTime]::UtcNow
}
while ([DateTime]::UtcNow -lt $deadline -and -not $process.HasExited) { Start-Sleep -Milliseconds 200; $process.Refresh() }
if (-not $process.HasExited) {
    if ($PreserveOnTimeout) {
        throw "Graceful shutdown did not complete within $TimeoutSeconds seconds. The authenticated process was preserved by request; inspect '$($metadata.stderr_log)'."
    }
    $ownedChildCount = Stop-WarRoomOwnedProcessTree -Metadata $metadata -Process $process
    Remove-Item -LiteralPath $metadataPath -Force
    Write-Warning "Graceful shutdown remained pending. Force-terminated authenticated War Room PID $($process.Id) and $ownedChildCount owned child process(es). Persisted databases were preserved; unfinished runs will be marked interrupted on restart."
    return
}
Remove-Item -LiteralPath $metadataPath -Force
Write-Host "War Room stopped cleanly. Persisted databases were preserved."
