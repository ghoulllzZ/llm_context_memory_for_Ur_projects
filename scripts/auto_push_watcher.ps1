param(
    [string]$RepoPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [int]$IntervalSeconds = 15,
    [switch]$RunOnce
)

$ErrorActionPreference = 'Stop'
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$repo = (Resolve-Path $RepoPath).Path
$gitDir = Join-Path $repo '.git'
if (-not (Test-Path $gitDir)) {
    exit 0
}

$gitExe = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
if (-not $gitExe) {
    foreach ($candidate in @('F:\GitForWindows\Git\cmd\git.exe', 'C:\Program Files\Git\cmd\git.exe')) {
        if (Test-Path $candidate) {
            $gitExe = $candidate
            break
        }
    }
}
if (-not $gitExe) {
    exit 1
}

$logPath = Join-Path $gitDir 'auto-push.log'

function Write-Log([string]$Message) {
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $logPath -Value "[$timestamp] $Message"
}

function Run-Git([string[]]$GitArgs) {
    $output = & $gitExe -C $repo $GitArgs 2>&1
    $exitCode = $LASTEXITCODE
    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = (($output | ForEach-Object { $_.ToString() }) -join "`n").Trim()
    }
}

function Invoke-AutoPushCycle {
    $origin = Run-Git @('remote', 'get-url', 'origin')
    if ($origin.ExitCode -ne 0) {
        Write-Log "origin remote not configured yet: $($origin.Output)"
        return
    }

    $branch = Run-Git @('symbolic-ref', '--quiet', '--short', 'HEAD')
    if ($branch.ExitCode -ne 0 -or -not $branch.Output) {
        Write-Log "no active branch to push: $($branch.Output)"
        return
    }

    $status = Run-Git @('status', '--porcelain', '--branch')
    if ($status.ExitCode -ne 0) {
        Write-Log "git status failed: $($status.Output)"
        return
    }

    if ($status.Output -match '\[ahead\s+(\d+)\]') {
        Write-Log "branch $($branch.Output) is ahead; pushing"
        $push = Run-Git @('push', 'origin', $branch.Output)
        if ($push.ExitCode -eq 0) {
            Write-Log "pushed branch $($branch.Output): $($push.Output)"
        } else {
            Write-Log "push failed for $($branch.Output): $($push.Output)"
        }
    } else {
        Write-Log "branch $($branch.Output) already in sync"
    }
}

Write-Log "auto-push watcher started for $repo using $gitExe"
if ($RunOnce) {
    Invoke-AutoPushCycle
    exit 0
}

while ($true) {
    try {
        Invoke-AutoPushCycle
    } catch {
        Write-Log $_.Exception.Message
    }
    Start-Sleep -Seconds $IntervalSeconds
}
