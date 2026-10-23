# Use quando as impressoras pararem de imprimir (rode pelo REPARAR_WINDOWS.bat).
# Os documentos pausados NAO sao retomados aqui: quem decide e o servico de cotas,
# senao eles imprimiriam sem descontar.

Write-Host ">> Reiniciando o spooler de impressao..."
Restart-Service Spooler -Force

Write-Host ">> Ligando o servico de cotas..."
Start-ScheduledTask -TaskName "CotasImpressao-Servico" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

$presos = Get-Printer | ForEach-Object { Get-PrintJob -PrinterName $_.Name -ErrorAction SilentlyContinue }
if ($presos) {
    $presos | Format-Table PrinterName, DocumentName, UserName, JobStatus -AutoSize
    if ((Read-Host "Cancelar esses documentos presos na fila? [s/N]") -match "^[sS]$") {
        $presos | Remove-PrintJob -ErrorAction SilentlyContinue
        Write-Host "   Fila limpa."
    }
}

$admin = Join-Path $env:ProgramFiles "CotasImpressao\cotas-admin.cmd"
if (Test-Path $admin) {
    Write-Host ""
    & $admin diagnostico
}
