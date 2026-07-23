$dll = "C:\Proyectos\New Arga Nesting Suite\dist\icon_handler\ArgaNestIconHandler.dll"
$a = [Reflection.AssemblyName]::GetAssemblyName($dll)
Write-Output "FULLNAME=$($a.FullName)"
Write-Output "CODEBASE=$([Uri]$dll).AbsoluteUri"
$clsid = "{B6E2C9A1-4D7F-4E8A-9C31-7A2F0D91E5B4}"
$class = "ArgaNestIconHandler.WorkspaceIconHandler"
$base = "HKCU:\Software\Classes\CLSID\$clsid"
New-Item -Path $base -Force | Out-Null
Set-ItemProperty -Path $base -Name "(default)" -Value "Arga Nest Workspace Icon Handler"
$inproc = Join-Path $base "InProcServer32"
New-Item -Path $inproc -Force | Out-Null
Set-ItemProperty -Path $inproc -Name "(default)" -Value "mscoree.dll"
New-ItemProperty -Path $inproc -Name "ThreadingModel" -Value "Both" -PropertyType String -Force | Out-Null
New-ItemProperty -Path $inproc -Name "Class" -Value $class -PropertyType String -Force | Out-Null
New-ItemProperty -Path $inproc -Name "Assembly" -Value $a.FullName -PropertyType String -Force | Out-Null
New-ItemProperty -Path $inproc -Name "RuntimeVersion" -Value "v4.0.30319" -PropertyType String -Force | Out-Null
New-ItemProperty -Path $inproc -Name "CodeBase" -Value ([Uri]$dll).AbsoluteUri -PropertyType String -Force | Out-Null
Write-Output "Registered HKCU CLSID $clsid"
