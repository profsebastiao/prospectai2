"""Busca no Google pelo Serper.dev para coletar evidências públicas da prospecção.

Cada consulta usa 1 crédito do Serper. A chave é da empresa (criptografada) e nunca vai para o navegador.
"""
import json
import logging
import re

from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from apps.accounts.gemini_credentials import CredencialIndisponivel, descriptografar_busca
from apps.accounts.models import IntegracaoGemini

from .ia_comum import USER_AGENT, EndpointFixo, IAIndisponivel, detalhe_seguro
from .models import LimiteGemini
from .perfis import web_url

logger = logging.getLogger(__name__)
ENDPOINT = "https://google.serper.dev/search"

SEM_CHAVE = "Conecte a busca no Google (Serper.dev) em Configurações → Integrações para buscar evidências automaticamente."
CHAVE_RECUSADA = "A chave do Serper.dev foi recusada. Confira se copiou a chave inteira do painel do Serper."
SEM_CREDITO = "A conta do Serper.dev ficou sem créditos de busca. Adicione créditos no painel do Serper ou cole as evidências manualmente."
LIMITE_DIA = "O limite diário de buscas no Google desta empresa foi atingido. Tente amanhã ou cole as evidências manualmente."
TEMPORARIA = "A busca no Google está temporariamente indisponível. Tente de novo em alguns minutos."


def limite_diario():
    try:
        return max(1, int(getattr(settings, "SERPER_DAILY_LIMIT", 30)))
    except (TypeError, ValueError):
        return 30


def _reservar(workspace_id, quantidade):
    dia = timezone.localdate()
    try:
        with transaction.atomic():
            contador, _ = LimiteGemini.objects.get_or_create(dia=dia, escopo=f"busca:empresa:{workspace_id}")
            atualizados = LimiteGemini.objects.filter(
                pk=contador.pk, chamadas__lte=limite_diario() - quantidade,
            ).update(chamadas=F("chamadas") + quantidade)
            if not atualizados:
                raise IAIndisponivel(LIMITE_DIA, "limite_busca")
    except OperationalError:
        raise IAIndisponivel(TEMPORARIA, "temporaria") from None


def _chave_da_empresa(workspace_id):
    integracao = IntegracaoGemini.objects.filter(workspace_id=workspace_id, busca_credencial__isnull=False).first()
    if not integracao:
        raise IAIndisponivel(SEM_CHAVE, "sem_busca")
    try:
        return descriptografar_busca(integracao)
    except CredencialIndisponivel:
        raise IAIndisponivel(
            "A chave de busca desta empresa não pôde ser lida. Verifique a chave mestre do servidor.", "armazenamento",
        ) from None


@sensitive_variables()
def _consultar(chave, consulta, quantidade=10):
    corpo = {"q": consulta[:300], "gl": "br", "hl": "pt-br", "num": quantidade}
    requisicao = Request(
        ENDPOINT, data=json.dumps(corpo, ensure_ascii=False).encode("utf-8"),
        headers={"X-API-KEY": chave, "Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with build_opener(EndpointFixo()).open(requisicao, timeout=30) as resposta:
            bruto = resposta.read(1_000_000)
        if chave.encode() in bruto:
            raise ValueError("secret echoed")
        dados = json.loads(bruto)
        if not isinstance(dados, dict):
            raise ValueError("invalid")
        return dados
    except HTTPError as exc:
        try:
            texto = exc.read(4000).decode("utf-8", "replace")
            mensagem = str((json.loads(texto) or {}).get("message", texto))
        except (ValueError, OSError, AttributeError):
            mensagem = ""
        detalhe = detalhe_seguro(mensagem, chave)
        minusculo = mensagem.lower()
        logger.warning("Serper: http=%s detalhe=%s", exc.code, detalhe or "-")
        if exc.code in {401, 403} or "api key" in minusculo or "unauthorized" in minusculo:
            raise IAIndisponivel(CHAVE_RECUSADA, "credencial") from None
        if exc.code == 402 or "credit" in minusculo:
            raise IAIndisponivel(SEM_CREDITO, "credito") from None
        if exc.code == 429:
            raise IAIndisponivel(f"{TEMPORARIA} O Serper limitou a velocidade das buscas.", "limite") from None
        raise IAIndisponivel(f"{TEMPORARIA}{f' Resposta do Serper (HTTP {exc.code}): {detalhe}' if detalhe else ''}", "temporaria") from None
    except (TimeoutError, URLError, OSError):
        logger.warning("Serper: falha de rede")
        raise IAIndisponivel(TEMPORARIA, "rede") from None
    except (ValueError, TypeError):
        logger.warning("Serper: resposta inválida")
        raise IAIndisponivel("A busca no Google devolveu uma resposta inválida. Tente de novo.", "resposta") from None


def _nota(item):
    nota, total = item.get("rating"), item.get("ratingCount") or item.get("reviews")
    if isinstance(nota, (int, float)) and isinstance(total, int):
        return f"Nota {str(nota).replace('.', ',')} ({total}) no Google"
    return ""


def _maps(item):
    cid = str(item.get("cid") or "")
    return f"https://maps.google.com/?cid={cid}" if re.fullmatch(r"\d{5,25}", cid) else ""


def fontes_da_resposta(dados):
    """Transforma a resposta do Serper em evidências {url, title, content, origem}."""
    fontes = []
    grafico = dados.get("knowledgeGraph")
    if isinstance(grafico, dict) and grafico.get("title"):
        atributos = grafico.get("attributes") if isinstance(grafico.get("attributes"), dict) else {}
        partes = [
            f"Perfil da empresa no Google: {grafico['title']}",
            str(grafico.get("type") or ""), _nota(grafico),
            *(f"{chave}: {valor}" for chave, valor in list(atributos.items())[:8]),
            f"Site: {grafico['website']}" if grafico.get("website") else "",
            str(grafico.get("description") or ""),
        ]
        url = web_url(str(grafico.get("website") or grafico.get("descriptionLink") or ""))
        fontes.append({"url": url, "title": str(grafico["title"])[:200],
                       "content": ". ".join(p for p in partes if p)[:2100], "origem": "google"})
    for local in (dados.get("places") or [])[:5]:
        if not isinstance(local, dict) or not local.get("title"):
            continue
        partes = [
            f"Google Maps: {local['title']}", str(local.get("category") or ""), _nota(local),
            str(local.get("address") or ""),
            f"Telefone: {local['phoneNumber']}" if local.get("phoneNumber") else "",
            f"Site: {local['website']}" if local.get("website") else "",
        ]
        fontes.append({"url": _maps(local) or web_url(str(local.get("website") or "")),
                       "title": str(local["title"])[:200], "content": ". ".join(p for p in partes if p)[:2100],
                       "origem": "google"})
    for item in (dados.get("organic") or [])[:10]:
        if not isinstance(item, dict):
            continue
        url = web_url(str(item.get("link") or ""))
        trecho = str(item.get("snippet") or "").strip()
        if not url or not trecho:
            continue
        links = [web_url(str(s.get("link") or "")) for s in item.get("sitelinks") or [] if isinstance(s, dict)]
        conteudo = trecho + ("\n" + "\n".join(l for l in links[:4] if l) if any(links) else "")
        fontes.append({"url": url, "title": str(item.get("title") or "")[:200], "content": conteudo[:2100],
                       "origem": "google", "published_date": str(item.get("date") or "")})
    return fontes


def buscar_google(consultas, workspace_id):
    """Busca até 3 consultas no Google e devolve as evidências encontradas (sem repetir links)."""
    consultas = [c.strip() for c in consultas if c and c.strip()][:3]
    if not consultas:
        return []
    chave = _chave_da_empresa(workspace_id)
    _reservar(workspace_id, len(consultas))
    vistas, fontes = set(), []
    for consulta in consultas:
        for fonte in fontes_da_resposta(_consultar(chave, consulta)):
            chave_fonte = fonte["url"] or fonte["title"]
            if chave_fonte in vistas:
                continue
            vistas.add(chave_fonte)
            fontes.append(fonte)
    return fontes[:20]


@sensitive_variables()
def testar_chave(chave):
    dados = _consultar(chave, "Serper Google Search API", quantidade=1)
    if not isinstance(dados.get("organic"), list):
        raise IAIndisponivel("O Serper não confirmou a busca com esta chave.", "resposta")
