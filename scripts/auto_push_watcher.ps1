param(
    [string]$RepoPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [int]$IntervalSeconds = 15
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path $RepoPath).Path
$gitDir = Join-Path $repo '.git'
if (-not (Test-Path $gitDir)) {
    exit 0
}

$logPath = Join-Path $gitDir 'auto-push.log'

function Write-Log([string]$Message) {
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $logPath -Value "[$timestamp] $Message"
}

function Run-Git([string[]]$Args, [switch]$Quiet) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'git'
    $psi.WorkingDirectory = $repo
    foreach ($arg in $Args) { [void]$psi.ArgumentList.Add($arg) }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if (-not $Quiet -and $stderr.Trim()) {
        Write-Log $stderr.Trim()
    }
    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        StdOut = $stdout.Trim()
        StdErr = $stderr.Trim()
    }
}

Write-Log "auto-push watcher started for $repo"
while ($true) {
    try {
        $hasOrigin = Run-Git @('remote', 'get-url', 'origin') -Quiet
        if ($hasOrigin.ExitCode -ne 0) {
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        $branch = Run-Git @('symbolic-ref', '--quiet', '--short', 'HEAD') -Quiet
        if ($branch.ExitCode -ne 0 -or -not $branch.StdOut) {
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        $status = Run-Git @('status', '--porcelain', '--branch') -Quiet
        if ($status.ExitCode -ne 0) {
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        if ($status.StdOut -match '\[ahead\s+(\d+)\]') {
            $push = Run-Git @('push', 'origin', $branch.StdOut)
            if ($push.ExitCode -eq 0) {
                Write-Log "pushed branch $($branch.StdOut)"
            }
        }
    } catch {
        Write-Log $_.Exception.Message
    }
    Start-Sleep -Seconds $IntervalSeconds
}
