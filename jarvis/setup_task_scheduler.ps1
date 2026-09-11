# Registers Jarvis for the current Windows user at logon.
# Run as the normal user, not Administrator.
#
# Inspect/disable later with:
#   Win+R -> taskschd.msc
#
# The action uses pythonw.exe, so no console window is shown.

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$Script = Join-Path $ProjectDir "jarvis_main.py"

if (-not (Test-Path $Script)) {
    throw "jarvis_main.py was not found in $ProjectDir"
}

$TaskName = "Jarvis Voice Assistant"

$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "`"$Script`"" `
    -WorkingDirectory $ProjectDir

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType InteractiveToken `
    -RunLevel Limited

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Starts the offline Jarvis wake-word voice assistant at Windows logon." `
    -Force

Write-Host "Registered: $TaskName"
Write-Host "Python: $Pythonw"
Write-Host "Script: $Script"
Write-Host "Open Task Scheduler with: taskschd.msc"
