param(
    [string]$TaskName = 'CodeX_GPT_Memory_AutoPush'
)

schtasks /Delete /TN $TaskName /F | Out-Null
Write-Output "Deleted scheduled task: $TaskName"
