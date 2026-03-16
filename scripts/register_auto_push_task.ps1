param(
    [string]$TaskName = 'CodeX_GPT_Memory_AutoPush',
    [string]$RepoPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot 'auto_push_watcher.ps1')).Path
$repoPath = (Resolve-Path $RepoPath).Path
$escapedScript = $scriptPath.Replace('"', '""')
$escapedRepo = $repoPath.Replace('"', '""')
$command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$escapedScript`" -RepoPath `"$escapedRepo`""

schtasks /Create /TN $TaskName /SC ONLOGON /TR $command /F | Out-Null
Write-Output "Registered scheduled task: $TaskName"
Write-Output "Command: $command"
