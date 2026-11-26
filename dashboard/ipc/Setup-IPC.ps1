<#
  Sets up a Windows IPC (our Phoenix Contact VL3 PPC) as the rig's always-on dashboard:
  email alerts, the daily summary and the history copied off the PLC every day.
  The guide is IPC-setup-guide.html at the top of the repo.

  Put this file, "Set up this IPC.bat" and M580-Machine-Health-Demo.exe in one folder (C:\M580Dashboard),
  then double-click the .bat. It asks for administrator rights and then:
    - checks the two network connections, and that nothing routes between them
    - starts the dashboard at power-up, hidden, in live mode (scheduled task "M580 Machine Health")
    - shows it full screen on the touchscreen when someone logs on (task "M580 Machine Health screen")
    - stops the PC sleeping
  Run it again at any time to repeat the checks. Options:
    -NoScreen     don't show the dashboard on the touchscreen at logon
    -CheckOnly    only run the network checks, change nothing
    -Remove       take both tasks away again
    -PlcIp x.x.x.x   the CPU's address, if it isn't 192.168.10.10
#>
param(
    [string]$PlcIp = "192.168.10.10",
    [switch]$NoScreen,
    [switch]$CheckOnly,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "M580 Machine Health"
$ScreenTask = "M580 Machine Health screen"
$Url = "http://127.0.0.1:8780"
$Folder = Split-Path -Parent $PSCommandPath
$Exe = Join-Path $Folder "M580-Machine-Health-Demo.exe"

# ---- administrator rights, asked for once ----
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $pass = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-PlcIp", $PlcIp)
    if ($NoScreen) { $pass += "-NoScreen" }
    if ($CheckOnly) { $pass += "-CheckOnly" }
    if ($Remove) { $pass += "-Remove" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $pass
    exit
}

$script:Problems = 0
function Ok($t)   { Write-Host "  OK    $t" -ForegroundColor Green }
function Bad($t)  { Write-Host "  FIX   $t" -ForegroundColor Red; $script:Problems++ }
function Info($t) { Write-Host "        $t" -ForegroundColor Gray }
function Done {
    Write-Host ""
    Read-Host "Press Enter to close"
    exit
}

if ($Remove) {
    foreach ($t in $TaskName, $ScreenTask) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Write-Host "Removed the task `"$t`"."
        }
    }
    Get-Process -Name "M580-Machine-Health-Demo" -ErrorAction SilentlyContinue | Stop-Process -Force
    Done
}

Write-Host ""
Write-Host "M580 Machine Health: IPC set-up" -ForegroundColor White
Write-Host "Folder: $Folder"
Write-Host ""

# ---- 1. the networks ----
Write-Host "Networks" -ForegroundColor White
$plcNet = $PlcIp -replace "\.\d+$", "."
$configs = Get-NetIPConfiguration | Where-Object { $_.IPv4Address -and $_.NetAdapter.Status -eq "Up" }
$plcSide = @($configs | Where-Object { $_.IPv4Address.IPAddress -like "$plcNet*" })
$otherSide = @($configs | Where-Object { $_.IPv4Address.IPAddress -notlike "$plcNet*" -and $_.IPv4Address.IPAddress -notlike "169.254.*" })

if ($plcSide.Count -eq 1) {
    $p = $plcSide[0]
    Ok "PLC side: $($p.InterfaceAlias), $($p.IPv4Address.IPAddress)"
    if ($p.IPv4DefaultGateway) {
        Bad "$($p.InterfaceAlias) has a gateway ($($p.IPv4DefaultGateway.NextHop)). Clear it: the PLC network must not be a way out."
    } else { Ok "no gateway on the PLC side" }
    if ((Get-NetIPInterface -InterfaceIndex $p.InterfaceIndex -AddressFamily IPv4).Dhcp -eq "Enabled") {
        Bad "$($p.InterfaceAlias) gets its address automatically. Give it a fixed one, for example $($plcNet)50 / 255.255.255.0."
    }
} elseif ($plcSide.Count -eq 0) {
    Bad "No connection on $($plcNet)x. Cable one port to the PLC network and give it a fixed address such as $($plcNet)50 / 255.255.255.0, no gateway."
} else {
    Bad "More than one connection is on $($plcNet)x ($(($plcSide | ForEach-Object InterfaceAlias) -join ', ')). Only the PLC side should be."
}

$outside = @($otherSide | Where-Object { $_.IPv4DefaultGateway })
if ($outside.Count -ge 1) {
    $o = $outside[0]
    Ok "outside: $($o.InterfaceAlias), $($o.IPv4Address.IPAddress), gateway $($o.IPv4DefaultGateway.NextHop)"
    if ($o.IPv4Address.IPAddress -like "192.168.10.*" -or $o.IPv4Address.IPAddress -like "192.168.20.*") {
        Bad "The outside connection is on 192.168.10.x or 20.x, the rig's own addresses. Use a network that isn't, or addresses will clash."
    }
} else {
    Bad "No connection to the outside (a second port or the USB Wi-Fi, set to get its address automatically). Email needs one."
}

$fwd = @(Get-NetIPInterface -AddressFamily IPv4 | Where-Object { $_.Forwarding -eq "Enabled" })
if ($fwd.Count) { Bad "Routing is on for $(($fwd | ForEach-Object InterfaceAlias) -join ', '). Turn it off: Set-NetIPInterface -Forwarding Disabled" }
else { Ok "the PC doesn't route between its connections" }

if (Get-NetAdapter | Where-Object { $_.InterfaceDescription -like "*Bridge*" }) {
    Bad "There is a network bridge. Delete it (Network Connections, right-click the bridge)."
} else { Ok "no network bridge" }

try {
    $share = New-Object -ComObject HNetCfg.HNetShare
    $shared = @()
    foreach ($c in $share.EnumEveryConnection) {
        if ($share.INetSharingConfigurationForINetConnection.Invoke($c).SharingEnabled) {
            $shared += $share.NetConnectionProps.Invoke($c).Name
        }
    }
    if ($shared.Count) { Bad "Internet Connection Sharing is on for $($shared -join ', '). Untick it on the adapter's Sharing tab." }
    else { Ok "Internet Connection Sharing is off" }
} catch { Info "couldn't check Internet Connection Sharing: check each adapter's Sharing tab is unticked" }

# ---- 2. can we reach both sides? ----
Write-Host ""
Write-Host "Reaching things" -ForegroundColor White
function Port($h, $n) {
    $c = New-Object Net.Sockets.TcpClient
    try { return $c.ConnectAsync($h, $n).Wait(3000) -and $c.Connected } catch { return $false } finally { $c.Close() }
}
if (Port $PlcIp 502) { Ok "the M580 at $PlcIp answers on Modbus (port 502)" }
else { Bad "no answer from the M580 at $PlcIp on port 502. Check the cable and the PLC side's address." }
if (Port "smtp.gmail.com" 587) { Ok "mail servers are reachable (smtp.gmail.com, port 587)" }
else { Info "couldn't reach smtp.gmail.com on port 587. Fine if we use another mail server; otherwise check the outside connection, or ask IT whether port 587 is blocked." }

if ($CheckOnly) {
    Write-Host ""
    if ($script:Problems) { Write-Host "$script:Problems thing(s) to fix." -ForegroundColor Red } else { Write-Host "All good." -ForegroundColor Green }
    Done
}

# ---- 3. the dashboard, at power-up ----
Write-Host ""
Write-Host "Setting up" -ForegroundColor White
if (-not (Test-Path $Exe)) {
    Bad "M580-Machine-Health-Demo.exe isn't in $Folder. Download it from the release and put it here, then run this again."
    Done
}
Unblock-File -Path $Exe -ErrorAction SilentlyContinue        # downloaded files are marked; this clears the mark

$action = New-ScheduledTaskAction -Execute $Exe -Argument "--live --no-browser" -WorkingDirectory $Folder
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "M580 Machine Health dashboard: email alerts and history, live from the PLC" -Force | Out-Null
Ok "the dashboard starts at power-up (task `"$TaskName`")"

# ---- 4. on the touchscreen, at logon ----
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($NoScreen) {
    if (Get-ScheduledTask -TaskName $ScreenTask -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $ScreenTask -Confirm:$false }
    Info "not showing it on the touchscreen (-NoScreen). Open $Url in a browser to see it."
} elseif ($edge) {
    $a2 = New-ScheduledTaskAction -Execute $edge -Argument "--kiosk $Url --edge-kiosk-type=fullscreen --no-first-run"
    $t2 = New-ScheduledTaskTrigger -AtLogOn
    $t2.Delay = "PT20S"                                       # give the dashboard time to start
    $p2 = New-ScheduledTaskPrincipal -GroupId "BUILTIN\Users" -RunLevel Limited
    Register-ScheduledTask -TaskName $ScreenTask -Action $a2 -Trigger $t2 -Principal $p2 -Force | Out-Null
    Ok "the touchscreen shows it full screen at logon (Alt+F4 closes it)"
} else {
    Info "Edge isn't installed, so it won't open on the screen by itself. Open $Url in any browser."
}

# ---- 5. never sleep ----
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
Ok "the PC doesn't sleep or hibernate"
Info "also in the BIOS: power on after a power cut (often 'AC power recovery' or 'Restore on AC power loss')"

# ---- 6. start it now ----
Get-Process -Name "M580-Machine-Health-Demo" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-ScheduledTask -TaskName $TaskName
$up = $false
foreach ($i in 1..20) {
    Start-Sleep -Seconds 1
    try { Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true; break } catch { }
}
if ($up) { Ok "the dashboard is running: $Url" } else { Bad "the dashboard didn't start. Run the .exe by double-clicking it to see why." }

Write-Host ""
if ($script:Problems) { Write-Host "$script:Problems thing(s) to fix above, then run this again." -ForegroundColor Red }
else { Write-Host "All set. Next: log off and on (or restart) to see it on the screen, then Setup tab, Email & text alerts (guide, step 5)." -ForegroundColor Green }
Done
