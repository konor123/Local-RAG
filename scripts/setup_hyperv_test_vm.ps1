param(
    [string]$IsoPath = "C:\Users\OSLENG\Downloads\Win11_25H2_Korean_x64_v2.iso",
    [string]$VMName = "OSL-RAG-v110-Test",
    [string]$VMRoot = "C:\Users\Public\Documents\Hyper-V\OSL-RAG-v110-Test",
    [int64]$MemoryStartupBytes = 8GB,
    [int64]$VhdSizeBytes = 80GB,
    [int]$ProcessorCount = 4
)

$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "    [OK] $m" -ForegroundColor Green }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Run this script from an elevated PowerShell session." }
if (-not (Test-Path -LiteralPath $IsoPath)) { throw "ISO not found: $IsoPath" }

Import-Module Hyper-V
$existing = Get-VM -Name $VMName -ErrorAction SilentlyContinue
if ($existing) {
    Step "VM already exists: $VMName"
    if ($existing.State -ne 'Off') {
        Stop-VM -Name $VMName -Force
        Ok "stopped existing VM"
    }
} else {
    Step "Preparing VM directory"
    New-Item -ItemType Directory -Path $VMRoot -Force | Out-Null
    $vhdPath = Join-Path $VMRoot "$VMName.vhdx"

    Step "Selecting Hyper-V switch"
    $switch = Get-VMSwitch -Name "Default Switch" -ErrorAction SilentlyContinue
    if (-not $switch) { $switch = Get-VMSwitch | Select-Object -First 1 }
    if (-not $switch) { throw "No Hyper-V virtual switch found." }
    Ok "using switch: $($switch.Name)"

    Step "Creating VM"
    New-VM -Name $VMName -Generation 2 -MemoryStartupBytes $MemoryStartupBytes -NewVHDPath $vhdPath -NewVHDSizeBytes $VhdSizeBytes -Path $VMRoot -SwitchName $switch.Name | Out-Null
    Set-VMProcessor -VMName $VMName -Count $ProcessorCount
    Set-VMMemory -VMName $VMName -DynamicMemoryEnabled $true -MinimumBytes 4GB -StartupBytes $MemoryStartupBytes -MaximumBytes $MemoryStartupBytes
    Ok "created VM with $ProcessorCount CPU(s), $($MemoryStartupBytes / 1GB)GB RAM, $($VhdSizeBytes / 1GB)GB disk"
}

Step "Configuring firmware and TPM"
Set-VMFirmware -VMName $VMName -EnableSecureBoot On -SecureBootTemplate "MicrosoftWindows"
try {
    Set-VMKeyProtector -VMName $VMName -NewLocalKeyProtector
    Enable-VMTPM -VMName $VMName
    Ok "enabled vTPM"
} catch {
    Write-Host "    [WARN] vTPM setup skipped or already configured: $($_.Exception.Message)" -ForegroundColor Yellow
}
Step "Attaching Windows ISO"
$dvd = Get-VMDvdDrive -VMName $VMName -ErrorAction SilentlyContinue | Select-Object -First 1
if ($dvd) {
    Set-VMDvdDrive -VMName $VMName -ControllerNumber $dvd.ControllerNumber -ControllerLocation $dvd.ControllerLocation -Path $IsoPath
} else {
    Add-VMDvdDrive -VMName $VMName -Path $IsoPath
    $dvd = Get-VMDvdDrive -VMName $VMName | Select-Object -First 1
}
Ok "attached ISO: $IsoPath"

Step "Setting DVD as first boot device"
$dvd = Get-VMDvdDrive -VMName $VMName | Select-Object -First 1
Set-VMFirmware -VMName $VMName -FirstBootDevice $dvd
Ok "DVD first boot configured"

Step "Enabling integration services"
Get-VMIntegrationService -VMName $VMName | Where-Object { $_.Name -in @('Guest Service Interface','Heartbeat','Key-Value Pair Exchange','Shutdown','Time Synchronization','VSS') } | Enable-VMIntegrationService -ErrorAction SilentlyContinue

Step "Starting VM"
Start-VM -Name $VMName
Ok "started VM"

Get-VM -Name $VMName | Select-Object Name, State, Generation, ProcessorCount, MemoryStartup, Path | Format-List
Write-Host "NEXT: Open Hyper-V Manager or run: vmconnect.exe localhost '$VMName'" -ForegroundColor Cyan
Write-Host "When Windows setup says 'Press any key to boot from CD or DVD', click the VM window and press a key." -ForegroundColor Yellow
