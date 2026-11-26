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


def _contexto(workspace_id):
    return f"prospectai:gemini:v1:{workspace_id}".encode("ascii")


@sensitive_variables()
def criptografar(workspace_id, chave):
    nonce = os.urandom(12)
    return nonce + AESGCM(_chaves()[0]).encrypt(nonce, chave.encode(), _contexto(workspace_id))


@sensitive_variables()
def descriptografar(integracao):
    dados = bytes(integracao.credencial)
    for chave in _chaves():
        try:
            return AESGCM(chave).decrypt(dados[:12], dados[12:], _contexto(integracao.workspace_id)).decode()
        except (InvalidTag, ValueError):
            continue
    raise CredencialIndisponivel(
        "A credencial desta empresa não pôde ser lida. Verifique a chave de criptografia do servidor."
    )


def pode_gerenciar_groq(user, workspace):
    if not workspace or not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or WorkspaceMembership.objects.filter(
        usuario=user, workspace=workspace, pode_gerenciar_gemini=True,
    ).exists()


def status_groq(workspace_id):
    # Do not fetch or pass ciphertext to a template. Vale para qualquer provedor (Gemini ou Groq).
    return IntegracaoGemini.objects.filter(workspace_id=workspace_id).values(
        "provedor", "ultimo_teste", "status_teste", "pesquisa_web", "modelo_pesquisa", "limite_diario",
    ).first()


def web_habilitada(config):
    from apps.leads.ia import web_habilitada as habilitada  # import tardio evita ciclo
    return habilitada(config)
