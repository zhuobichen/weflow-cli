# Register / update the official-account daily report tasks.
# Idempotent: running it again overwrites the definitions with the same values.
#
# Why this cannot always be done for you: writing to Task Scheduler is refused in a
# restricted shell / agent sandbox, and refused as a *class* - measured on 2026-10-19 with
# an elevated Administrator token, all three write paths said "Access is denied" while
# reads kept working:
#     schtasks /create ...                      -> Access is denied
#     (New-Object -ComObject Schedule.Service)   -> RegisterTaskDefinition 0x80070005
#     New-ScheduledTaskAction / -SettingsSet ... -> 拒绝访问  (even the in-memory builders)
# So "I can see the tasks but cannot create them" is not an illusion, and it is not fixed
# by "run as administrator". The one reliable route is a terminal the sandbox does not
# supervise: double-click scripts\install_tasks.cmd (it asks for elevation itself), or run
# this file from your own PowerShell window.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\scripts\install_tasks.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\scripts\install_tasks.ps1 -Uninstall
#
# The scheduled runs:
#   WeFlow Daily       23:30                -> TODAY near end of day
#   WeFlow Daily Noon  13:00                -> TODAY so far (half a day of pushes)
#   WeFlow Daily Catchup at logon           -> YESTERDAY if the night run was missed
#
# The scheduled end-of-day pass is at 23:30. The logon catch-up targets yesterday,
# so posts arriving in the final half-hour are included on the next login.
#
# This file is intentionally ASCII-only: Windows PowerShell 5.1 reads a BOM-less file as
# the ANSI codepage, so non-ASCII text here turns into mojibake *and* a parse error.
param(
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo 'scripts\daily_task.ps1'
if (-not (Test-Path $script)) { throw "not found: $script (this script belongs in the repo's scripts\ folder)" }

$isAdmin = (New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host 'Need elevation: reopen PowerShell as administrator, then run this again.' -ForegroundColor Yellow
    exit 1
}

if ($Uninstall) {
    foreach ($name in @('WeFlow Daily Catchup', 'WeFlow Daily Noon', 'WeFlow Daily')) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "removed: $name"
        }
    }
    exit 0
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$psExe = 'powershell.exe'
$common = '-NoProfile -ExecutionPolicy Bypass -File ' + $script

# Launch through wscript.exe, not powershell.exe directly.
#
# A scheduled action that runs powershell.exe is a *console* program: Windows creates the console
# window before PowerShell can hide it, so every run flashes a black box - and while a game is in
# the foreground that is a real interruption. wscript.exe is a GUI process with no console at all,
# and run_hidden.vbs starts PowerShell with window style 0, so nothing appears.
$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
$vbs = Join-Path $PSScriptRoot 'run_hidden.vbs'
if (-not (Test-Path $vbs)) {
    Write-Host "Missing launcher: $vbs" -ForegroundColor Red
    exit 1
}
$action = '//B //Nologo "' + $vbs + '"'

# End of day: process today at 23:30. A same-day logon catch-up is deliberately separate
# so that waking after midnight processes yesterday rather than silently relabeling it.
Register-ScheduledTask -TaskName 'WeFlow Daily' `
    -Action (New-ScheduledTaskAction -Execute $wscript -Argument ($action + ' night')) `
    -Trigger (New-ScheduledTaskTrigger -Daily -At '23:30') `
    -Settings $settings `
    -Description 'WeChat daily report for today (23:30, no window)' -Force | Out-Null

# Noon: today so far. No logon trigger: a morning logon must not produce a thin report
# and set the marker that tells the actual 13:00 pass to skip itself.
Register-ScheduledTask -TaskName 'WeFlow Daily Noon' `
    -Action (New-ScheduledTaskAction -Execute $wscript -Argument ($action + ' noon')) `
    -Trigger (New-ScheduledTaskTrigger -Daily -At '13:00') `
    -Settings $settings `
    -Description 'WeChat daily report for today so far (13:00, no window)' -Force | Out-Null

# Missed-run recovery: after logon, fill yesterday and include any posts after 23:30.
Register-ScheduledTask -TaskName 'WeFlow Daily Catchup' `
    -Action (New-ScheduledTaskAction -Execute $wscript -Argument ($action + ' catchup')) `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
    -Settings $settings `
    -Description 'WeChat daily report catch-up for yesterday at logon' -Force | Out-Null

foreach ($name in @('WeFlow Daily', 'WeFlow Daily Noon', 'WeFlow Daily Catchup')) {
    $t = Get-ScheduledTask -TaskName $name
    $i = Get-ScheduledTaskInfo -TaskName $name
    Write-Host ("{0,-20} {1,-8} next {2}" -f $t.TaskName, $t.State, $i.NextRunTime) -ForegroundColor Green
}
Write-Host ''
Write-Host 'Run one now instead of waiting:'
Write-Host '  Start-ScheduledTask -TaskName "WeFlow Daily Noon"   # today so far'
Write-Host '  Start-ScheduledTask -TaskName "WeFlow Daily"        # today, 23:30 pass'
Write-Host '  Start-ScheduledTask -TaskName "WeFlow Daily Catchup" # yesterday after missed night run'
Write-Host 'Logs: <repo>\logs\daily-<date>[-noon|-night].log'
