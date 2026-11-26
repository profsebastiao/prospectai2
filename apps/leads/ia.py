"""Ponto único de acesso à IA da prospecção (provedor: OpenRouter)."""
from apps.accounts.models import IntegracaoGemini

from . import openrouter
from .ia_comum import IAIndisponivel, limite_global, limite_padrao  # noqa: F401  (reexportados)

SEM_CHAVE = "Conecte o OpenRouter em Configurações → Integrações para usar a prospecção com IA."
WEB_INDISPONIVEL = openrouter.WEB_INDISPONIVEL

PROVEDOR = {
    "codigo": "openrouter",
    "nome": "OpenRouter",
    "console_url": "https://openrouter.ai/keys",
    "console_nome": "OpenRouter",
    "busca_nome": "busca na web do OpenRouter",
}


def info():
    try:
        modelo = openrouter.modelo_configurado()
    except IAIndisponivel:
        modelo = ""
    return {**PROVEDOR, "modelo": modelo, "busca_disponivel": openrouter.busca_disponivel()}


def web_habilitada(config):
    if not config or config.get("provedor") != "openrouter" or not config.get("pesquisa_web"):
        return False
    return config.get("modelo_pesquisa") == info()["modelo"] and openrouter.busca_disponivel()


def _exigir_provedor(workspace_id):
    provedor = IntegracaoGemini.objects.filter(workspace_id=workspace_id).values_list("provedor", flat=True).first()
    if provedor != "openrouter":
        raise IAIndisponivel(SEM_CHAVE, "sem_chave")


def gerar(workspace_id, instrucao, dados, *, schema=None):
    _exigir_provedor(workspace_id)
    return openrouter.gerar(workspace_id, instrucao, dados, schema=schema)


def pesquisar(consulta, workspace_id):
    _exigir_provedor(workspace_id)
    return openrouter.pesquisar_openrouter(consulta, workspace_id)


def testar_chave(workspace_id, chave, limite, *, web=False):
    return openrouter.testar_chave(workspace_id, chave, limite, web=web)


def contexto(request):
    """Context processor: nome do provedor de IA exibido nas telas."""
    return {"ia_nome": PROVEDOR["nome"]}
