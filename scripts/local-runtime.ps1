function Read-WarRoomMetadata([string]$MetadataPath) {
    try { $metadata = Get-Content -LiteralPath $MetadataPath -Raw | ConvertFrom-Json }
    catch { throw "Launcher metadata is unreadable at '$MetadataPath'; no process was trusted or stopped." }
    $required = @(
        "schema_version", "run_id", "pid", "process_started_at_utc", "port",
        "database", "frontend", "shutdown_file", "stdout_log", "stderr_log"
    )
    foreach ($name in $required) {
        if ($null -eq $metadata.$name -or [string]::IsNullOrWhiteSpace([string]$metadata.$name)) {
            throw "Launcher metadata at '$MetadataPath' is missing required field '$name'; ownership is ambiguous."
        }
    }
    if ([int]$metadata.schema_version -ne 1) { throw "Launcher metadata uses an unsupported schema version." }
    if ([int]$metadata.pid -lt 1) { throw "Launcher metadata contains an invalid PID." }
    if ([int]$metadata.port -lt 1 -or [int]$metadata.port -gt 65535) { throw "Launcher metadata contains an invalid port." }
    try { $null = [DateTime]::Parse([string]$metadata.process_started_at_utc).ToUniversalTime() }
    catch { throw "Launcher metadata contains an invalid process creation timestamp." }
    return $metadata
}

function Get-WarRoomOwnedProcess($Metadata) {
    $process = Get-Process -Id ([int]$Metadata.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $recorded = [DateTime]::Parse([string]$Metadata.process_started_at_utc).ToUniversalTime()
    try { $actual = $process.StartTime.ToUniversalTime() }
    catch { throw "PID $($Metadata.pid) exists, but its creation time cannot be verified; no process was trusted or stopped." }
    if ([Math]::Abs(($actual - $recorded).TotalSeconds) -gt 2) {
        throw "PID $($Metadata.pid) was reused or does not match launcher metadata; no process was trusted or stopped."
    }
    return $process
}

function Assert-WarRoomHealthIdentity(
    [int]$Port,
    [string]$ExpectedRunId,
    [int]$ExpectedProcessId = 0,
    [int]$TimeoutSeconds = 2
) {
    $uri = "http://127.0.0.1:$Port/api/v1/health"
    try { $health = Invoke-RestMethod -Uri $uri -TimeoutSec $TimeoutSeconds }
    catch { throw "The launcher-owned process did not provide a healthy War Room identity at $uri; no process was trusted or stopped." }
    if (
        [string]$health.api_schema_version -ne "1.0" -or
        [string]$health.service -ne "fantasy-football-application" -or
        [string]$health.execution_mode -ne "local_background_m6_2" -or
        $health.local_only -ne $true -or
        [int]$health.process_id -lt 1 -or
        [string]$health.launcher_run_id -ne $ExpectedRunId -or
        ($ExpectedProcessId -gt 0 -and [int]$health.process_id -ne $ExpectedProcessId)
    ) {
        throw "The process at $uri returned an unexpected application identity; no process was trusted or stopped."
    }
    return $health
}

function Assert-WarRoomPortOwner([int]$Port, [int]$ProcessId) {
    try {
        $owned = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -ErrorAction Stop |
            Where-Object { $_.OwningProcess -eq $ProcessId } |
            Select-Object -First 1
    } catch {
        throw "The loopback listener owner on port $Port could not be verified; no process was trusted or stopped."
    }
    if ($null -eq $owned) {
        throw "PID $ProcessId does not own the configured loopback listener on port $Port; no process was trusted or stopped."
    }
}
