# Instala o controle de cotas de impressao no Windows 10/11.
# Jeito mais facil: de dois cliques em INSTALAR_WINDOWS.bat (ele chama este script
# como Administrador). Se o Python nao existir, ele e instalado sozinho.

$ErrorActionPreference = "Continue"
$VERSAO_PY = "3.12.10"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host "Rode pelo INSTALAR_WINDOWS.bat (ou como Administrador)." -ForegroundColor Red; exit 1 }

function Falhar($msg) {
    Write-Host ""; Write-Host "ERRO: $msg" -ForegroundColor Red; exit 1
}

# Outro controle de cotas junto com este pode bloquear ou contar impressoes em dobro
$outros = Get-Service -ErrorAction SilentlyContinue |
    Where-Object { "$($_.Name) $($_.DisplayName)" -match "PaperCut|Pharos|PrinterLogic|PrintManager" }
if ($outros) {
    Write-Host "ATENCAO: outro controle de impressao instalado: $($outros.DisplayName -join ', ')" -ForegroundColor Yellow
    Write-Host "Os dois juntos podem bloquear ou contar as impressoes em dobro." -ForegroundColor Yellow
    if ((Read-Host "Continuar mesmo assim? [s/N]") -notmatch "^[sS]$") { Falhar "instalacao cancelada." }
}

# ---------------------------------------------------------------- Python
# Usa um Python instalado para TODOS os usuarios (em Arquivos de Programas),
# porque o servico roda como SYSTEM e o painel roda em cada usuario.
function Achar-Python {
    $raizes = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    $achados = foreach ($r in $raizes) {
        Get-ChildItem -Path $r -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "python.exe" } | Where-Object { Test-Path $_ }
    }
    # o mais novo primeiro (Python313 > Python312 > ...)
    $achados | Sort-Object { [int](($_ -replace '.*\\Python3(\d+).*', '$1')) } -Descending | Select-Object -First 1
}

$py = Achar-Python
if (-not $py) {
    Write-Host ">> Python nao encontrado. Instalando Python $VERSAO_PY para todos os usuarios..."
    $ok = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Python.Python.3.12 --scope machine --silent --accept-package-agreements --accept-source-agreements
        $py = Achar-Python
        $ok = [bool]$py
    }
    if (-not $ok) {
        $url = "https://www.python.org/ftp/python/$VERSAO_PY/python-$VERSAO_PY-amd64.exe"
        $inst = Join-Path $env:TEMP "python-$VERSAO_PY-amd64.exe"
        Write-Host ">> Baixando $url"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $url -OutFile $inst -UseBasicParsing -ErrorAction Stop
        } catch { Falhar "nao consegui baixar o Python. Verifique a internet." }
        $p = Start-Process -FilePath $inst -Wait -PassThru -ArgumentList `
            "/quiet InstallAllUsers=1 PrependPath=1 Include_tcltk=1 Include_pip=1 Include_launcher=1"
        Remove-Item $inst -ErrorAction SilentlyContinue
        if ($p.ExitCode -ne 0) { Falhar "o instalador do Python terminou com codigo $($p.ExitCode)." }
        $py = Achar-Python
    }
    if (-not $py) { Falhar "o Python foi instalado mas nao foi encontrado em Arquivos de Programas." }
}
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
Write-Host ">> Python: $py"

& $py -c "import tkinter" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Falhar "este Python nao tem tkinter (reinstale marcando 'tcl/tk and IDLE')." }

Write-Host ">> Instalando pywin32..."
& $py -m pip install --upgrade --disable-pip-version-check pywin32
& $py -c "import win32print" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Falhar "o pywin32 nao foi instalado (verifique a internet)." }
& $py -m pywin32_postinstall -install -silent 2>&1 | Out-Null

# ---------------------------------------------------------------- arquivos
$origem  = $PSScriptRoot
$destino = Join-Path $env:ProgramFiles "CotasImpressao"
$dados   = Join-Path $env:ProgramData "CotasImpressao"

Write-Host ">> Copiando para $destino..."
Get-ScheduledTask -TaskName "CotasImpressao-Servico" -ErrorAction SilentlyContinue | Stop-ScheduledTask
New-Item -ItemType Directory -Force -Path $destino | Out-Null
Copy-Item -Recurse -Force "$origem\cotas" $destino -ErrorAction Stop
Copy-Item -Force "$origem\servico.pyw", "$origem\painel.pyw", "$origem\admin.py" $destino

# Pasta de dados: SYSTEM e Administradores escrevem; usuarios comuns so leem.
New-Item -ItemType Directory -Force -Path $dados | Out-Null
icacls $dados /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" | Out-Null
Push-Location $destino
& $py -c "from cotas import config, banco; config.garantir_pastas(); banco.inicializar()"
Pop-Location

# ---------------------------------------------------------------- servico
Write-Host ">> Registrando o servico (tarefa agendada como SYSTEM)..."
$acao = New-ScheduledTaskAction -Execute $pyw -Argument "`"$destino\servico.pyw`"" -WorkingDirectory $destino
# Dois gatilhos na mesma tarefa:
#  1) ao ligar o computador (antes de qualquer login);
#  2) a cada 1 minuto, para sempre: se o servico tiver parado, ele volta.
#     Como a tarefa usa "IgnoreNew", se ele ja estiver rodando nada acontece.
$aoIniciar = New-ScheduledTaskTrigger -AtStartup
$vigia = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$gatilho = @($aoIniciar, $vigia)
$principal = New-ScheduledTaskPrincipal -UserId "S-1-5-18" -LogonType ServiceAccount -RunLevel Highest
$opcoes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
          -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
          -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "CotasImpressao-Servico" -Action $acao -Trigger $gatilho `
    -Principal $principal -Settings $opcoes -Force -ErrorAction Stop | Out-Null
Start-ScheduledTask -TaskName "CotasImpressao-Servico"
Start-Sleep -Seconds 3
$t = Get-ScheduledTask -TaskName "CotasImpressao-Servico"
Write-Host ">> Tarefa do servico: $($t.State) | inicia com o Windows e e verificada a cada minuto"

# ---------------------------------------------------------------- painel
Write-Host ">> Criando atalhos do painel..."
$shell = New-Object -ComObject WScript.Shell
$inicializar = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\StartUp\Cota de impressao.lnk"
$menu        = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\Minha cota de impressao.lnk"
# Icone na Area de Trabalho publica (aparece para todos os usuarios)
$nomeIcone   = "Cota de Impress$([char]0x00E3)o.lnk"
$desktop     = Join-Path ([Environment]::GetFolderPath("CommonDesktopDirectory")) $nomeIcone
foreach ($par in @(@($inicializar, "--minimizado"), @($menu, ""), @($desktop, ""))) {
    $lnk = $shell.CreateShortcut($par[0])
    $lnk.TargetPath = $pyw
    $lnk.Arguments = "`"$destino\painel.pyw`" $($par[1])"
    $lnk.WorkingDirectory = $destino
    $lnk.IconLocation = "$env:SystemRoot\System32\imageres.dll,46"
    $lnk.Description = "Veja quantas folhas voce ainda pode imprimir hoje"
    $lnk.Save()
}

# ---------------------------------------------------------------- admin
Set-Content -Encoding ASCII -Path "$destino\cotas-admin.cmd" -Value "@echo off`r`ncd /d `"$destino`"`r`n`"$py`" admin.py %*"
$pathMaq = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($pathMaq -notlike "*$destino*") {
    [Environment]::SetEnvironmentVariable("Path", "$pathMaq;$destino", "Machine")
}

# Abre o painel agora, como o usuario comum (via explorer = sem privilegio de admin).
Start-Sleep -Seconds 3
Write-Host ">> Abrindo o painel..."
Start-Process explorer.exe -ArgumentList "`"$menu`""

Write-Host ""
Write-Host "Pronto!" -ForegroundColor Green
Write-Host "  Ver saldos (novo terminal como Administrador):  cotas-admin listar"
Write-Host "  Mudar o limite padrao:                         cotas-admin padrao 20"
Write-Host "  Log do servico:  $dados\servico.log"
Write-Host "O painel ja abriu nesta conta e vai abrir sozinho no login de todos os usuarios."
Write-Host "Para abrir de novo: icone Cota de Impressao na Area de Trabalho."
Write-Host "Na primeira abertura o painel pede para criar a senha do administrador."
