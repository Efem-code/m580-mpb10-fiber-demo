<#
  Lets the rig laptop log in to this IPC over SSH, from the rig's control network only.
  Run once, at the IPC, through "Enable remote access.bat" (it asks for administrator rights).

    - installs Windows' own OpenSSH server: from Windows Update when the IPC has internet (a phone
      hotspot is fine), or from an OpenSSH-Win64*.msi placed next to this file when it doesn't
    - lets in only the laptop's key (the *.pub file(s) next to this file), no passwords
    - accepts SSH only from 192.168.10.x, the control network; never from the outside connection
    - makes PowerShell the shell
  "Enable remote access.bat -Off" stops the SSH server and switches it off again.
#>
param([switch]$Off, [string]$AllowFrom = "192.168.10.0/24")

$ErrorActionPreference = "Stop"
$Folder = Split-Path -Parent $PSCommandPath
$RuleName = "M580 rig SSH (control network only)"

$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $pass = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-AllowFrom", $AllowFrom)
    if ($Off) { $pass += "-Off" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $pass
    exit
}
function Ok($t)  { Write-Host "  OK    $t" -ForegroundColor Green }
function Bad($t) { Write-Host "  FIX   $t" -ForegroundColor Red }
function Done { Write-Host ""; Read-Host "Press Enter to close"; exit }

if ($Off) {
    Stop-Service sshd -ErrorAction SilentlyContinue
    Set-Service sshd -StartupType Disabled -ErrorAction SilentlyContinue
    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Ok "SSH is off and won't start again"
    Done
}

Write-Host ""
Write-Host "Remote access for the rig laptop" -ForegroundColor White

# ---- 1. the OpenSSH server ----
if (-not (Get-Service sshd -ErrorAction SilentlyContinue)) {
    $msi = Get-ChildItem -Path $Folder -Filter "OpenSSH-Win64*.msi" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($msi) {
        Start-Process msiexec.exe -Wait -ArgumentList "/i `"$($msi.FullName)`" /qn ADDLOCAL=Server"
    } else {
        try {
            Add-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0" | Out-Null
        } catch {
            Bad "Couldn't install the OpenSSH server: no internet, and no OpenSSH-Win64 .msi in this folder."
            Write-Host "        Connect the IPC to a phone hotspot and run this again, or put OpenSSH-Win64-*.msi"
            Write-Host "        (github.com/PowerShell/Win32-OpenSSH/releases) next to this file."
            Done
        }
    }
}
if (-not (Get-Service sshd -ErrorAction SilentlyContinue)) { Bad "The OpenSSH server didn't install."; Done }
Ok "OpenSSH server installed"

# ---- 2. keys only, no passwords ----
$keys = Get-ChildItem -Path $Folder -Filter "*.pub" | ForEach-Object { (Get-Content $_.FullName -Raw).Trim() } | Where-Object { $_ -match "^ssh-" }
if (-not $keys) { Bad "No laptop key (*.pub) next to this file."; Done }
$ssh = Join-Path $env:ProgramData "ssh"
New-Item -ItemType Directory -Force -Path $ssh | Out-Null
$auth = Join-Path $ssh "administrators_authorized_keys"
[IO.File]::WriteAllText($auth, (($keys -join "`n") + "`n"), (New-Object Text.UTF8Encoding $false))
icacls.exe $auth /inheritance:r /grant "*S-1-5-32-544:F" /grant "*S-1-5-18:F" | Out-Null      # Administrators, SYSTEM
Ok "the laptop's key is let in ($(@($keys).Count) key(s))"

Start-Service sshd                                    # first start writes sshd_config
$cfg = Join-Path $ssh "sshd_config"
$text = Get-Content $cfg -Raw
foreach ($pair in @(@("PasswordAuthentication", "no"), @("PubkeyAuthentication", "yes"))) {
    $line = "$($pair[0]) $($pair[1])"
    if ($text -match "(?m)^#?\s*$($pair[0])\b.*$") { $text = $text -replace "(?m)^#?\s*$($pair[0])\b.*$", $line }
    else { $text = "$line`r`n" + $text }
}
Set-Content -Path $cfg -Value $text -Encoding ascii
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -PropertyType String -Force `
    -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" | Out-Null
Ok "passwords refused, keys only; the shell is PowerShell"

# ---- 3. only from the control network ----
Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue | Disable-NetFirewallRule
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress $AllowFrom -Action Allow | Out-Null
Ok "SSH accepted from $AllowFrom only"

Set-Service sshd -StartupType Automatic
Restart-Service sshd
Ok "SSH server running, and starts with Windows"

# ---- what the laptop needs ----
$here = Get-NetIPConfiguration | Where-Object { $_.IPv4Address.IPAddress -like "192.168.10.*" } | Select-Object -First 1
Write-Host ""
if ($here) { Write-Host "From the laptop: ssh $env:USERNAME@$($here.IPv4Address.IPAddress)" -ForegroundColor Green }
else { Write-Host "This IPC has no 192.168.10.x address yet: give the PLC-side port 192.168.10.50 (guide, step 3)." -ForegroundColor Yellow }
Write-Host "To switch it off later: Enable remote access.bat -Off"
Done
