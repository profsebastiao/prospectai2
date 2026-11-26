"""Ponto único de acesso à IA da prospecção (provedor: Gemini)."""
from apps.accounts.models import IntegracaoGemini

from . import gemini
from .ia_comum import IAIndisponivel, limite_global, limite_padrao  # noqa: F401  (reexportados)

SEM_CHAVE = "Conecte o Gemini em Configurações → Integrações para usar a prospecção com IA."
WEB_INDISPONIVEL = gemini.WEB_INDISPONIVEL

PROVEDOR = {
    "codigo": "gemini",
    "nome": "Gemini",
    "console_url": "https://aistudio.google.com/apikey",
    "console_nome": "Google AI Studio",
    "busca_nome": "busca no Google",
}


def info():
    try:
        modelo = gemini.modelo_configurado()
    except IAIndisponivel:
        modelo = ""
    return {**PROVEDOR, "modelo": modelo, "busca_disponivel": gemini.busca_disponivel()}


def web_habilitada(config):
    if not config or config.get("provedor") != "gemini" or not config.get("pesquisa_web"):
        return False
    return config.get("modelo_pesquisa") == info()["modelo"] and gemini.busca_disponivel()


def _exigir_gemini(workspace_id):
    provedor = IntegracaoGemini.objects.filter(workspace_id=workspace_id).values_list("provedor", flat=True).first()
    if provedor != "gemini":
        raise IAIndisponivel(SEM_CHAVE, "sem_chave")


def gerar(workspace_id, instrucao, dados, *, schema=None):
    _exigir_gemini(workspace_id)
    return gemini.gerar(workspace_id, instrucao, dados, schema=schema)


def pesquisar(consulta, workspace_id):
    _exigir_gemini(workspace_id)
    return gemini.pesquisar_gemini(consulta, workspace_id)


def testar_chave(workspace_id, chave, limite, *, web=False):
    return gemini.testar_chave(workspace_id, chave, limite, web=web)


def contexto(request):
    """Context processor: nome do provedor de IA exibido nas telas."""
    return {"ia_nome": PROVEDOR["nome"]}
