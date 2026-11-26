"""Ponto único de acesso à IA da prospecção.

O provedor usado em cada chamada é o da credencial salva pela empresa (Gemini ou Groq).
IA_PROVEDOR define qual provedor a tela de integração conecta quando uma chave nova é salva.
"""
from django.conf import settings

from apps.accounts.models import IntegracaoGemini

from . import gemini, groq
from .groq import IAIndisponivel, limite_global, limite_padrao  # noqa: F401  (reexportados)

SEM_CHAVE = "Conecte a IA desta empresa em Configurações → Integrações para usar a prospecção com IA."
WEB_INDISPONIVEL = "A busca na web da IA está indisponível para esta conexão. Você pode adicionar evidências manualmente."

PROVEDORES = {
    "gemini": {
        "codigo": "gemini",
        "nome": "Gemini",
        "console_url": "https://aistudio.google.com/apikey",
        "console_nome": "Google AI Studio",
        "busca_nome": "busca no Google",
    },
    "groq": {
        "codigo": "groq",
        "nome": "Groq",
        "console_url": "https://console.groq.com/keys",
        "console_nome": "GroqCloud",
        "busca_nome": "busca de navegador da Groq",
    },
}


def provedor_ativo():
    codigo = str(getattr(settings, "IA_PROVEDOR", "gemini") or "gemini").strip().lower()
    return codigo if codigo in PROVEDORES else "gemini"


def modelo(codigo):
    if codigo == "gemini":
        try:
            return gemini.modelo_configurado()
        except IAIndisponivel:
            return str(getattr(settings, "GEMINI_MODEL", ""))
    return settings.GROQ_MODEL


def busca_disponivel(codigo):
    if codigo == "gemini":
        return gemini.busca_disponivel()
    return settings.GROQ_MODEL in groq.MODELOS_COM_BUSCA


def info(codigo=None):
    codigo = codigo if codigo in PROVEDORES else provedor_ativo()
    return {**PROVEDORES[codigo], "modelo": modelo(codigo), "busca_disponivel": busca_disponivel(codigo)}


def web_habilitada(config):
    if not config or config.get("provedor") not in PROVEDORES or not config.get("pesquisa_web"):
        return False
    codigo = config["provedor"]
    return config.get("modelo_pesquisa") == modelo(codigo) and busca_disponivel(codigo)


def _modulo(workspace_id):
    integracao = IntegracaoGemini.objects.filter(workspace_id=workspace_id).only("provedor").first()
    if not integracao or integracao.provedor not in PROVEDORES:
        raise IAIndisponivel(SEM_CHAVE, "sem_chave")
    return gemini if integracao.provedor == "gemini" else groq


def gerar(workspace_id, instrucao, dados, *, schema=None):
    return _modulo(workspace_id).gerar(workspace_id, instrucao, dados, schema=schema)


def pesquisar(consulta, workspace_id):
    modulo = _modulo(workspace_id)
    if modulo is gemini:
        return gemini.pesquisar_gemini(consulta, workspace_id)
    return groq.pesquisar_groq(consulta, workspace_id)


def testar_chave(codigo, workspace_id, chave, limite, *, web=False):
    modulo = gemini if codigo == "gemini" else groq
    return modulo.testar_chave(workspace_id, chave, limite, web=web)


def contexto(request):
    """Context processor: nome do provedor de IA da empresa ativa (o salvo ou, sem credencial, o padrão)."""
    workspace = getattr(request, "workspace", None)
    codigo = None
    if workspace is not None:
        codigo = IntegracaoGemini.objects.filter(workspace=workspace).values_list("provedor", flat=True).first()
    return {"ia_nome": info(codigo)["nome"]}
