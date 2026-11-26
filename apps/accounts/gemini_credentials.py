import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .models import IntegracaoGemini, WorkspaceMembership


class CredencialIndisponivel(Exception):
    pass


def _chaves():
    try:
        chaves = [
            base64.b64decode(chave.strip(), altchars=b"-_", validate=True)
            for chave in settings.GEMINI_ENCRYPTION_KEYS.split(",") if chave.strip()
        ]
        if not chaves or any(len(chave) != 32 for chave in chaves):
            raise ValueError
        return chaves
    except (ValueError, binascii.Error):
        raise CredencialIndisponivel(
            "O armazenamento seguro das credenciais de IA não está configurado no servidor."
        ) from None


def _contexto(workspace_id, finalidade="gemini"):
    # A finalidade separa as credenciais: a chave da IA não abre no lugar da chave de busca e vice-versa.
    return f"prospectai:{finalidade}:v1:{workspace_id}".encode("ascii")


@sensitive_variables()
def criptografar(workspace_id, chave, finalidade="gemini"):
    nonce = os.urandom(12)
    return nonce + AESGCM(_chaves()[0]).encrypt(nonce, chave.encode(), _contexto(workspace_id, finalidade))


@sensitive_variables()
def descriptografar(integracao):
    return _abrir(bytes(integracao.credencial), integracao.workspace_id, "gemini")


@sensitive_variables()
def descriptografar_busca(integracao):
    if not integracao.busca_credencial:
        raise CredencialIndisponivel("A busca no Google não está conectada para esta empresa.")
    return _abrir(bytes(integracao.busca_credencial), integracao.workspace_id, "serper")


@sensitive_variables()
def _abrir(dados, workspace_id, finalidade):
    for chave in _chaves():
        try:
            return AESGCM(chave).decrypt(dados[:12], dados[12:], _contexto(workspace_id, finalidade)).decode()
        except (InvalidTag, ValueError):
            continue
    raise CredencialIndisponivel(
        "A credencial desta empresa não pôde ser lida. Verifique a chave de criptografia do servidor."
    )


def pode_gerenciar_ia(user, workspace):
    if not workspace or not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or WorkspaceMembership.objects.filter(
        usuario=user, workspace=workspace, pode_gerenciar_gemini=True,
    ).exists()


def busca_conectada(workspace_id):
    return IntegracaoGemini.objects.filter(workspace_id=workspace_id, busca_credencial__isnull=False).exists()


def status_ia(workspace_id):
    # Do not fetch or pass ciphertext to a template.
    return IntegracaoGemini.objects.filter(workspace_id=workspace_id).values(
        "provedor", "ultimo_teste", "status_teste", "pesquisa_web", "modelo_pesquisa", "limite_diario",
    ).first()


def web_habilitada(config):
    from apps.leads.ia import web_habilitada as habilitada  # import tardio evita ciclo
    return habilitada(config)
