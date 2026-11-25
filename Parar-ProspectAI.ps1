# Encerra o servidor local do ProspectAI (porta 8002).
$ErrorActionPreference = "Stop"
$porta = 8002
$conexoes = Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue
if (-not $conexoes) {
    Write-Host "Nenhum servidor do ProspectAI está rodando na porta $porta."
    exit 0
}
foreach ($id in ($conexoes | Select-Object -ExpandProperty OwningProcess -Unique)) {
    $processo = Get-Process -Id $id -ErrorAction SilentlyContinue
    if (-not $processo) { continue }
    if ($processo.ProcessName -notmatch "^python") {
        Write-Host "A porta $porta está com outro programa ($($processo.ProcessName)). Nada foi encerrado." -ForegroundColor Yellow
        continue
    }
    Stop-Process -Id $id -Force
    Write-Host "Servidor do ProspectAI encerrado (processo $id)."
}
