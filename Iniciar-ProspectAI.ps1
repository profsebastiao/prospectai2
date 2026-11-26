param(
    [switch]$SkipDesktopShortcut
)

$ErrorActionPreference = "Stop"
$projectDir = $PSScriptRoot
$python = Join-Path $projectDir ".venv-windows\Scripts\python.exe"
$baseUrl = "http://127.0.0.1:8002"
$serverAddress = "127.0.0.1:8002"

function Test-LocalProspectAI {
    try {
        $response = Invoke-WebRequest -Uri "$baseUrl/login/" -TimeoutSec 3 -UseBasicParsing
        return $response.StatusCode -eq 200 -and $response.Content.Contains("ProspectAI")
    } catch {
        return $false
    }
}

function Open-ProspectAI {
    try {
        Start-Process -FilePath $baseUrl
        Write-Host "ProspectAI esta disponivel em $baseUrl/"
    } catch {
        Write-Host "O servidor esta ativo. Abra o navegador e acesse $baseUrl/"
    }
}

function Test-AIKeyring([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $true }
    foreach ($part in $Value.Split(",")) {
        $key = $part.Trim()
        if (-not $key -or $key -match "\s" -or $key -notmatch "^[A-Za-z0-9+/_-]+={0,2}$") { return $false }
        $standard = $key.Replace("-", "+").Replace("_", "/")
        if ($standard.Length % 4 -ne 0) { return $false }
        try { $bytes = [Convert]::FromBase64String($standard) } catch { return $false }
        if ($bytes.Length -ne 32) { return $false }
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
    return $true
}

function Ensure-DesktopShortcut {
    $desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop)
    $shortcutPath = Join-Path $desktop "ProspectAI.lnk"
    if (Test-Path -LiteralPath $shortcutPath) { return }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $projectDir "Iniciar-ProspectAI.cmd"
    $shortcut.WorkingDirectory = $projectDir
    $shortcut.Description = "Iniciar o ProspectAI local"
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll, 13"
    $shortcut.Save()
}

try {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python do ambiente .venv-windows não foi encontrado em $python."
    }

    if (-not $SkipDesktopShortcut) {
        try { Ensure-DesktopShortcut }
        catch { Write-Host "Não foi possível criar o atalho automaticamente. O lançador continua disponível nesta pasta." }
    }

    if (Test-LocalProspectAI) {
        if ($env:GEMINI_ENCRYPTION_KEYS) {
            Write-Host "O ProspectAI já está aberto. Para carregar uma chave mestre alterada, encerre o servidor e execute novamente."
        } else {
            Write-Host "O ProspectAI já está aberto. A integração OpenRouter pode ser carregada ao reiniciar o servidor com a chave mestre."
        }
        Write-Host "Para carregar atualizações do código, execute Parar-ProspectAI.cmd e depois este lançador de novo." -ForegroundColor Yellow
        Open-ProspectAI
        exit 0
    }

    $tcp = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $tcp.BeginConnect("127.0.0.1", 8002, $null, $null)
        if ($connect.AsyncWaitHandle.WaitOne(800)) {
            try { $tcp.EndConnect($connect) } catch { }
        }
        $portBusy = $tcp.Connected
    } finally {
        $tcp.Dispose()
    }
    if ($portBusy) {
        throw "A porta 8002 está ocupada por outro processo. Feche-o antes de iniciar o ProspectAI."
    }

    $keyring = $env:GEMINI_ENCRYPTION_KEYS
    if ($keyring -and -not (Test-AIKeyring $keyring)) {
        throw "GEMINI_ENCRYPTION_KEYS invalida. Ela deve conter uma chave mestre Base64 de 32 bytes, nao uma chave de API."
    }
    if (-not $keyring) {
        while ($true) {
            $secureMaster = Read-Host "Chave mestre AES-256 em Base64 (nao e a chave do OpenRouter; Enter inicia sem IA)" -AsSecureString
            $keyring = [System.Net.NetworkCredential]::new("", $secureMaster).Password
            Remove-Variable secureMaster -ErrorAction SilentlyContinue
            if (-not $keyring) { break }
            if ($keyring.StartsWith("sk-or-") -or $keyring.StartsWith("AIza") -or $keyring.StartsWith("gsk_")) {
                Write-Host "Essa e a chave da API de IA. Digite a chave mestre de criptografia neste prompt; a chave do OpenRouter e cadastrada dentro do ProspectAI depois que ele abrir." -ForegroundColor Yellow
                $keyring = $null
                continue
            }
            if (Test-AIKeyring $keyring) { break }
            Write-Host "Formato incorreto. Use a chave mestre Base64 de 32 bytes guardada anteriormente. A chave do OpenRouter nao serve aqui." -ForegroundColor Yellow
            $keyring = $null
        }
    }
    if ($keyring) { $env:GEMINI_ENCRYPTION_KEYS = $keyring }
    else { Remove-Item Env:GEMINI_ENCRYPTION_KEYS -ErrorAction SilentlyContinue }
    Remove-Variable secureMaster -ErrorAction SilentlyContinue
    $keyring = $null

    if (-not $env:OPENROUTER_MODEL) { $env:OPENROUTER_MODEL = "openrouter/free" }
    if (-not $env:IA_COMPANY_DAILY_LIMIT) { $env:IA_COMPANY_DAILY_LIMIT = "10" }
    if (-not $env:IA_GLOBAL_DAILY_LIMIT) { $env:IA_GLOBAL_DAILY_LIMIT = "100" }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdout = Join-Path $env:TEMP "prospectai-$stamp.out.log"
    $stderr = Join-Path $env:TEMP "prospectai-$stamp.err.log"
    $server = Start-Process -FilePath $python `
        -ArgumentList @("manage.py", "runserver", $serverAddress, "--noreload") `
        -WorkingDirectory $projectDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($server.HasExited) { break }
        if (Test-LocalProspectAI) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        $diagnostic = if (Test-Path -LiteralPath $stderr) { (Get-Content -LiteralPath $stderr -Tail 12) -join "`n" } else { "" }
        throw "O servidor não iniciou. Verifique o ambiente Python e as configurações locais.`n$diagnostic"
    }

    Open-ProspectAI
    if ($env:GEMINI_ENCRYPTION_KEYS) {
        Write-Host "ProspectAI iniciado. A chave mestre permanece apenas no ambiente do processo do servidor."
    } else {
        Write-Host "ProspectAI iniciado para o CRM. O OpenRouter fica desconectado até reiniciar com a chave mestre."
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
