$ErrorActionPreference = 'Continue'
Import-Module Hyper-V
$VMName = 'OSL-RAG-v110-Test'
$out = 'C:\Users\OSLENG\Desktop\projects\OSL RAG Internal\logs\hyperv-vm-status.txt'
New-Item -ItemType Directory -Path (Split-Path -Parent $out) -Force | Out-Null
'=== VM STATUS ===' | Set-Content -LiteralPath $out
Get-VM -Name $VMName | Select-Object Name, State, Generation, ProcessorCount, MemoryStartup, Path | Format-List | Out-String | Add-Content -LiteralPath $out
'=== DVD ===' | Add-Content -LiteralPath $out
Get-VMDvdDrive -VMName $VMName | Format-List | Out-String | Add-Content -LiteralPath $out
'=== FIRMWARE ===' | Add-Content -LiteralPath $out
Get-VMFirmware -VMName $VMName | Select-Object VMName, SecureBoot, SecureBootTemplate, PreferredNetworkBootProtocol | Format-List | Out-String | Add-Content -LiteralPath $out
'=== TPM ===' | Add-Content -LiteralPath $out
Get-VMTPM -VMName $VMName | Format-List | Out-String | Add-Content -LiteralPath $out
