[CmdletBinding()]
param(
    [int]$Port = $(if ($env:WAR_ROOM_PORT) { [int]$env:WAR_ROOM_PORT } else { 8000 }),
    [string]$Database = $(if ($env:WAR_ROOM_DATABASE) { $env:WAR_ROOM_DATABASE } else { "outputs\fantasy_football.sqlite3" }),
    [string]$Frontend = $(if ($env:WAR_ROOM_FRONTEND) { $env:WAR_ROOM_FRONTEND } else { "frontend\dist" }),
    [string]$LogDirectory = $(if ($env:WAR_ROOM_LOG_DIRECTORY) { $env:WAR_ROOM_LOG_DIRECTORY } else { "outputs\logs" }),
    [string]$RuntimeDirectory = $(if ($env:WAR_ROOM_RUNTIME_DIRECTORY) { $env:WAR_ROOM_RUNTIME_DIRECTORY } else { "outputs\runtime" }),
    [string]$PythonExecutable = ".venv\Scripts\python.exe",
    [switch]$NoBrowser,
    [int]$ReadinessTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "local-runtime.ps1")
function Resolve-RepoPath([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) { return [IO.Path]::GetFullPath($Value) }
    return [IO.Path]::GetFullPath((Join-Path $repo $Value))
}
function Test-Port([int]$Number) {
    $client = New-Object Net.Sockets.TcpClient
    try { $client.Connect("127.0.0.1", $Number); return $true } catch { return $false } finally { $client.Dispose() }
}

if ($Port -lt 1 -or $Port -gt 65535) { throw "Port must be between 1 and 65535." }
$python = Resolve-RepoPath $PythonExecutable
$databasePath = Resolve-RepoPath $Database
$frontendPath = Resolve-RepoPath $Frontend
$logPath = Resolve-RepoPath $LogDirectory
$runtimePath = Resolve-RepoPath $RuntimeDirectory
$metadataPath = Join-Path $runtimePath "war-room-server.json"
$shutdownPath = Join-Path $runtimePath "war-room-shutdown.txt"
$appUrl = "http://127.0.0.1:$Port/"

if (-not (Test-Path $python -PathType Leaf)) { throw "Prepared Python executable is missing at '$python'. Run .\scripts\setup-local.ps1 first." }
if (-not (Test-Path (Join-Path $frontendPath "index.html") -PathType Leaf)) { throw "Production frontend build is missing at '$frontendPath'. Run setup-local.ps1." }
New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($databasePath)), $logPath, $runtimePath | Out-Null

if (Test-Path $metadataPath) {
    $existing = Read-WarRoomMetadata $metadataPath
    $process = Get-WarRoomOwnedProcess $existing
    if ($process) {
        if ([int]$existing.port -ne $Port) {
            throw "War Room is already launcher-owned on port $($existing.port). Stop it before starting a different port."
        }
        if ([string]$existing.database -ne $databasePath -or [string]$existing.frontend -ne $frontendPath) {
            throw "War Room is already running with different database or frontend paths. Stop it before changing configuration."
        }
        $null = Assert-WarRoomProcessCommandIdentity $existing $process
        if (-not (Test-WarRoomPortOwner -Port $Port -ProcessId $process.Id)) {
            throw "The launcher-owned War Room process is alive without its listener. Run .\scripts\stop-local.ps1 to perform authenticated recovery, then start again."
        }
        Assert-WarRoomPortOwner -Port $Port -ProcessId $process.Id
        $null = Assert-WarRoomHealthIdentity -Port $Port -ExpectedRunId ([string]$existing.run_id) -ExpectedProcessId $process.Id
        Write-Host "War Room is already running at $appUrl"
        if (-not $NoBrowser) { Start-Process $appUrl }
        return
    }
    Remove-Item -LiteralPath $metadataPath -Force
}
if (Test-Port $Port) { throw "Port $Port is occupied by a process not owned by this launcher. Choose -Port or set WAR_ROOM_PORT; no process was terminated." }

$runId = [guid]::NewGuid().ToString("N")
Remove-Item -LiteralPath $shutdownPath -Force -ErrorAction SilentlyContinue
$stdout = Join-Path $logPath "war-room-$runId.stdout.log"
$stderr = Join-Path $logPath "war-room-$runId.stderr.log"
$arguments = @(
    "-m", "fantasy_football.app.server",
    "--database", ('"' + $databasePath + '"'),
    "--frontend", ('"' + $frontendPath + '"'),
    "--port", "$Port",
    "--shutdown-file", ('"' + $shutdownPath + '"'),
    "--run-id", $runId,
    "--stdout-log", ('"' + $stdout + '"'),
    "--stderr-log", ('"' + $stderr + '"')
)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repo -WindowStyle Hidden -PassThru
try {
    $processRecord = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction Stop
    if ($null -eq $processRecord -or [string]::IsNullOrWhiteSpace([string]$processRecord.CommandLine)) {
        throw "missing command line"
    }
} catch {
    $launched = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    if ($launched) { Stop-Process -Id $launched.Id -Force -ErrorAction SilentlyContinue }
    throw "The launched War Room process command identity could not be recorded safely."
}
$metadata = [ordered]@{ schema_version = 2; run_id = $runId; pid = $process.Id; process_started_at_utc = $process.StartTime.ToUniversalTime().ToString("o"); port = $Port; database = $databasePath; frontend = $frontendPath; shutdown_file = $shutdownPath; stdout_log = $stdout; stderr_log = $stderr; python_executable = [IO.Path]::GetFullPath([string]$process.Path); python_launcher = $python; command_line_sha256 = Get-WarRoomTextSha256 -Value ([string]$processRecord.CommandLine) }
$temporaryMetadata = "$metadataPath.tmp"
$metadata | ConvertTo-Json | Set-Content -LiteralPath $temporaryMetadata -Encoding UTF8
Move-Item -LiteralPath $temporaryMetadata -Destination $metadataPath -Force

$deadline = [DateTime]::UtcNow.AddSeconds($ReadinessTimeoutSeconds)
$ready = $false
while ([DateTime]::UtcNow -lt $deadline) {
    try {
        $health = Assert-WarRoomHealthIdentity -Port $Port -ExpectedRunId $runId
        $serverProcess = Get-Process -Id ([int]$health.process_id) -ErrorAction Stop
        Assert-WarRoomPortOwner -Port $Port -ProcessId $serverProcess.Id
        $metadata.pid = $serverProcess.Id
        $metadata.process_started_at_utc = $serverProcess.StartTime.ToUniversalTime().ToString("o")
        $metadata.python_executable = [IO.Path]::GetFullPath([string]$serverProcess.Path)
        $serverRecord = Get-CimInstance Win32_Process -Filter "ProcessId = $($serverProcess.Id)" -ErrorAction Stop
        if ($null -eq $serverRecord -or [string]::IsNullOrWhiteSpace([string]$serverRecord.CommandLine)) {
            throw "The healthy War Room process command identity could not be recorded safely."
        }
        $metadata.command_line_sha256 = Get-WarRoomTextSha256 -Value ([string]$serverRecord.CommandLine)
        $metadata | ConvertTo-Json | Set-Content -LiteralPath $temporaryMetadata -Encoding UTF8
        Move-Item -LiteralPath $temporaryMetadata -Destination $metadataPath -Force
        $process = $serverProcess
        $ready = $true
        break
    } catch { Start-Sleep -Milliseconds 200 }
}
if (-not $ready) {
    $runId | Set-Content -LiteralPath $shutdownPath -Encoding ASCII
    try { Wait-Process -Id $process.Id -Timeout 5 -ErrorAction SilentlyContinue } catch { }
    $remaining = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    if (-not $remaining) { Remove-Item -LiteralPath $metadataPath -Force -ErrorAction SilentlyContinue }
    throw "War Room did not become healthy within $ReadinessTimeoutSeconds seconds. Review '$stderr' and '$stdout'."
}
Write-Host "War Room is healthy at $appUrl"
Write-Host "Data: $databasePath"
Write-Host "Logs: $logPath"
if (-not $NoBrowser) {
    try { Start-Process $appUrl } catch { Write-Warning "The browser could not be opened. Navigate manually to $appUrl" }
}
