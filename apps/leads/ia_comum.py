"""Peças comuns da integração de IA: erro padrão, limites diários e transporte HTTP."""
import re
from urllib.request import HTTPRedirectHandler

from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from .models import LimiteGemini

USER_AGENT = "ProspectAI/1.0 (+https://sebastiao.dev.br)"
LIMITE_INTERNO = (
    "O limite diário de chamadas de IA desta empresa foi atingido. "
    "Aumente o limite na tela de integração ou tente novamente amanhã."
)
TEMPORARIA = "A IA está temporariamente indisponível. Seus dados foram mantidos. Tente novamente."
BLOQUEIO_REDE = (
    "O provedor de IA recusou a conexão antes de verificar a chave (bloqueio de firewall). "
    "Tente de novo em alguns minutos; se continuar, verifique VPN, proxy ou antivírus que filtre a internet."
)


class IAIndisponivel(Exception):
    def __init__(self, mensagem, codigo="indisponivel"):
        super().__init__(mensagem)
        self.codigo = codigo


class EndpointFixo(HTTPRedirectHandler):
    """Não segue redirecionamentos: a chave nunca é enviada a outro endereço."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _inteiro_positivo(nome, padrao):
    try:
        valor = int(getattr(settings, nome, padrao))
        if valor < 1:
            raise ValueError
        return valor
    except (TypeError, ValueError):
        raise IAIndisponivel(f"A configuração {nome} está inválida.", "limite") from None


def limite_global():
    return _inteiro_positivo("IA_GLOBAL_DAILY_LIMIT", 100)


def limite_padrao():
    return min(_inteiro_positivo("IA_COMPANY_DAILY_LIMIT", 10), limite_global())


def reservar_chamada(workspace_id, limite):
    dia = timezone.localdate()
    try:
        teto = limite_global()
        with transaction.atomic():
            total, _ = LimiteGemini.objects.get_or_create(dia=dia, escopo="global:ia")
            contador, _ = LimiteGemini.objects.get_or_create(dia=dia, escopo=f"ia:empresa:{workspace_id}")
            if not LimiteGemini.objects.filter(pk=total.pk, chamadas__lt=teto).update(chamadas=F("chamadas") + 1):
                raise IAIndisponivel(LIMITE_INTERNO, "limite_global")
            if not LimiteGemini.objects.filter(pk=contador.pk, chamadas__lt=limite).update(
                chamadas=F("chamadas") + 1,
            ):
                raise IAIndisponivel(LIMITE_INTERNO, "limite")
    except OperationalError:
        raise IAIndisponivel(TEMPORARIA, "temporaria") from None


def detalhe_seguro(texto, chave):
    """Trecho curto da resposta do provedor, sem a chave e sem caracteres de controle."""
    texto = str(texto or "")
    if chave:
        texto = texto.replace(chave, "[chave]")
    texto = re.sub(r"(?:AIza|gsk_)[A-Za-z0-9_\-]+", "[chave]", texto)
    texto = re.sub(r"[^\x20-\x7e -ÿ]+", " ", texto)
    return " ".join(texto.split())[:220]
