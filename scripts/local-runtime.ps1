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
    if ([int]$metadata.schema_version -notin @(1, 2)) { throw "Launcher metadata uses an unsupported schema version." }
    if (
        [int]$metadata.schema_version -eq 2 -and
        [string]::IsNullOrWhiteSpace([string]$metadata.python_executable)
    ) { throw "Launcher metadata is missing required field 'python_executable'; ownership is ambiguous." }
    if (
        [int]$metadata.schema_version -eq 2 -and
        [string]$metadata.command_line_sha256 -notmatch '^[0-9a-fA-F]{64}$'
    ) { throw "Launcher metadata is missing a valid command-line identity; ownership is ambiguous." }
    if ([int]$metadata.pid -lt 1) { throw "Launcher metadata contains an invalid PID." }
    if ([int]$metadata.port -lt 1 -or [int]$metadata.port -gt 65535) { throw "Launcher metadata contains an invalid port." }
    try { $null = [DateTime]::Parse([string]$metadata.process_started_at_utc).ToUniversalTime() }
    catch { throw "Launcher metadata contains an invalid process creation timestamp." }
    return $metadata
}

function Get-WarRoomTextSha256([string]$Value) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
        return ([BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally { $algorithm.Dispose() }
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

function Assert-WarRoomProcessCommandIdentity($Metadata, $Process) {
    try {
        $record = Get-CimInstance Win32_Process -Filter "ProcessId = $($Process.Id)" -ErrorAction Stop
    } catch {
        throw "PID $($Process.Id) exists, but its command identity cannot be verified; no process was trusted or stopped."
    }
    if ($null -eq $record -or [string]::IsNullOrWhiteSpace([string]$record.CommandLine)) {
        throw "PID $($Process.Id) has no verifiable command line; no process was trusted or stopped."
    }
    $required = @(
        "fantasy_football.app.server", "--run-id", [string]$Metadata.run_id,
        "--port", [string]$Metadata.port, "--database", [string]$Metadata.database,
        "--frontend", [string]$Metadata.frontend,
        "--shutdown-file", [string]$Metadata.shutdown_file
    )
    foreach ($value in $required) {
        if (-not ([string]$record.CommandLine).Contains([string]$value)) {
            throw "PID $($Process.Id) command identity does not match launcher metadata; no process was trusted or stopped."
        }
    }
    if ([int]$Metadata.schema_version -eq 2) {
        $actualCommandIdentity = Get-WarRoomTextSha256 -Value ([string]$record.CommandLine)
        if (-not [string]::Equals(
            $actualCommandIdentity,
            [string]$Metadata.command_line_sha256,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "PID $($Process.Id) command line changed or does not match launcher metadata; no process was trusted or stopped."
        }
        try { $actualExecutable = [IO.Path]::GetFullPath([string]$Process.Path) }
        catch { throw "PID $($Process.Id) executable identity cannot be verified; no process was trusted or stopped." }
        $expectedExecutable = [IO.Path]::GetFullPath([string]$Metadata.python_executable)
        if (-not [string]::Equals($actualExecutable, $expectedExecutable, [StringComparison]::OrdinalIgnoreCase)) {
            throw "PID $($Process.Id) executable does not match launcher metadata; no process was trusted or stopped."
        }
    }
    return $record
}

function Test-WarRoomPortOwner([int]$Port, [int]$ProcessId) {
    try {
        $owned = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -ErrorAction Stop |
            Where-Object { $_.OwningProcess -eq $ProcessId } |
            Select-Object -First 1
    } catch { return $false }
    return $null -ne $owned
}

function Get-WarRoomDescendantProcessRecords([int]$RootProcessId) {
    try { $all = @(Get-CimInstance Win32_Process -ErrorAction Stop) }
    catch { throw "Launcher-owned child processes could not be enumerated; forced recovery was not attempted." }
    $pending = [Collections.Generic.Queue[int]]::new()
    $pending.Enqueue($RootProcessId)
    $records = [Collections.Generic.List[object]]::new()
    while ($pending.Count -gt 0) {
        $parentId = $pending.Dequeue()
        foreach ($item in $all | Where-Object { [int]$_.ParentProcessId -eq $parentId }) {
            try {
                $child = Get-Process -Id ([int]$item.ProcessId) -ErrorAction Stop
                $started = $child.StartTime.ToUniversalTime()
            } catch { continue }
            $records.Add([pscustomobject]@{ pid = $child.Id; started_at_utc = $started })
            $pending.Enqueue($child.Id)
        }
    }
    return @($records)
}

function Stop-WarRoomOwnedProcessTree($Metadata, $Process, [int]$ExitTimeoutSeconds = 10) {
    $current = Get-WarRoomOwnedProcess $Metadata
    if (-not $current) { return 0 }
    $null = Assert-WarRoomProcessCommandIdentity $Metadata $current
    $descendants = @(Get-WarRoomDescendantProcessRecords -RootProcessId $Process.Id)
    $validatedDescendants = [Collections.Generic.List[object]]::new()
    foreach ($record in $descendants | Sort-Object -Property pid -Descending) {
        $child = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
        if (-not $child) { continue }
        try { $actualStart = $child.StartTime.ToUniversalTime() }
        catch { throw "Child PID $($record.pid) identity became unverifiable; forced recovery stopped safely." }
        if ([Math]::Abs(($actualStart - [DateTime]$record.started_at_utc).TotalSeconds) -gt 2) {
            throw "Child PID $($record.pid) was reused; forced recovery stopped safely."
        }
        $validatedDescendants.Add($record)
    }
    # Stop the authenticated root before its validated descendants so it cannot
    # create new workers after the lineage snapshot was taken.
    if ($current) { Stop-Process -Id $current.Id -Force -ErrorAction Stop }
    foreach ($record in $validatedDescendants) {
        $child = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
        if ($child) { Stop-Process -Id $child.Id -Force -ErrorAction Stop }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds($ExitTimeoutSeconds)
    do {
        $remaining = @($Process.Id) + @($descendants | ForEach-Object { [int]$_.pid }) |
            Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
        if ($remaining.Count -eq 0) { return $validatedDescendants.Count }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Authenticated War Room process recovery did not terminate every owned process."
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
    if (-not (Test-WarRoomPortOwner -Port $Port -ProcessId $ProcessId)) {
        throw "The loopback listener owner on port $Port could not be verified; no process was trusted or stopped."
    }
}
