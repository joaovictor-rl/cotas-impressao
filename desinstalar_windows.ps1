# Remove o controle de cotas (PowerShell como Administrador).
#   .\desinstalar_windows.ps1          mantem o historico
#   .\desinstalar_windows.ps1 -Tudo    apaga tambem config e historico
param([switch]$Tudo)

Stop-ScheduledTask -TaskName "CotasImpressao-Servico" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "CotasImpressao-Servico" -Confirm:$false -ErrorAction SilentlyContinue
Get-Process pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -like "*CotasImpressao*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue

# Retoma trabalhos que ficaram pausados pelo servico.
Get-Printer | ForEach-Object {
    Get-PrintJob -PrinterName $_.Name -ErrorAction SilentlyContinue |
        Where-Object { $_.JobStatus -like "*Paused*" } | Resume-PrintJob -ErrorAction SilentlyContinue
}

Remove-Item -Force -ErrorAction SilentlyContinue `
    (Join-Path ([Environment]::GetFolderPath("CommonDesktopDirectory")) "Cota de Impress$([char]0x00E3)o.lnk"),
    (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\StartUp\Cota de impressao.lnk"),
    (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\Minha cota de impressao.lnk")

$destino = Join-Path $env:ProgramFiles "CotasImpressao"
Remove-Item -Recurse -Force $destino -ErrorAction SilentlyContinue
$pathMaq = [Environment]::GetEnvironmentVariable("Path", "Machine")
[Environment]::SetEnvironmentVariable("Path", (($pathMaq -split ";") | Where-Object { $_ -and $_ -ne $destino }) -join ";", "Machine")

if ($Tudo) { Remove-Item -Recurse -Force (Join-Path $env:ProgramData "CotasImpressao") -ErrorAction SilentlyContinue }
Write-Host "Desinstalado."
