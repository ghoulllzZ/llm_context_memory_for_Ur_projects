param(
    [string]$TaskName = 'CodeX_GPT_Memory_AutoPush'
)

$ErrorActionPreference = 'Stop'
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Deleted scheduled task: $TaskName"
} else {
    Write-Output "Scheduled task not found: $TaskName"
}
