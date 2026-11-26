import re
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods

from apps.accounts.gemini_credentials import (
    CredencialIndisponivel, criptografar, descriptografar, pode_gerenciar_groq, status_groq, web_habilitada,
)
from apps.accounts.models import EventoIntegracaoGemini, IntegracaoGemini
from apps.leads.ia import (
    PROVEDORES, SEM_CHAVE, IAIndisponivel, info, limite_global, limite_padrao, provedor_ativo, testar_chave,
)


def _conexao_segura(request):
    host = urlsplit("//" + request.get_host()).hostname
    loopback = {"127.0.0.1", "::1", "localhost"}
    return request.is_secure() or (host in loopback and request.META.get("REMOTE_ADDR") in loopback)


def _auditar(request, evento):
    EventoIntegracaoGemini.objects.create(
        workspace=request.workspace, usuario=request.user, evento=evento,
    )


def _estado(request, aviso="", sucesso=False):
    workspace = request.workspace
    config = status_groq(workspace.pk)
    novo = info(provedor_ativo())
    salvo = info(config["provedor"]) if config and config["provedor"] in PROVEDORES else None
    return render(request, "integracao_groq.html", {
        "config": config, "aviso": aviso, "sucesso": sucesso,
        "novo": novo, "salvo": salvo,
        "trocando": bool(config) and (salvo is None or salvo["codigo"] != novo["codigo"]),
        "pode_gerenciar": pode_gerenciar_groq(request.user, workspace),
        "web_habilitada": web_habilitada(config),
        "busca_web_disponivel": bool(salvo and salvo["busca_disponivel"]),
        "modelo": salvo["modelo"] if salvo else novo["modelo"],
        "limite_global": limite_global(),
        "primeiro": request.GET.get("primeiro") == "1",
    })


def _alterar(request, atual, evento, **dados):
    with transaction.atomic():
        if atual:
            alteradas = IntegracaoGemini.objects.filter(
                pk=atual.pk, workspace=request.workspace, versao=atual.versao,
            ).update(**dados, versao=F("versao") + 1)
            if not alteradas:
                raise CredencialIndisponivel("A integração mudou em outra aba. Atualize a página antes de tentar novamente.")
        else:
            try:
                with transaction.atomic():
                    IntegracaoGemini.objects.create(workspace=request.workspace, **dados)
            except IntegrityError:
                raise CredencialIndisponivel("Esta empresa já recebeu uma configuração. Atualize a página.") from None
        _auditar(request, evento)


@sensitive_post_parameters("chave")
@sensitive_variables()
@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def integracao_groq(request):
    if request.workspace is None:
        return redirect("workspace_select")
    if request.method == "GET":
        return _estado(request)
    if not pode_gerenciar_groq(request.user, request.workspace):
        return HttpResponseForbidden("A gestão da integração exige permissão na empresa ativa.")
    if not _conexao_segura(request):
        return HttpResponseForbidden("Use HTTPS para alterar esta integração fora do localhost.")

    acao = request.POST.get("acao")
    if acao not in {"testar_nova", "salvar", "testar_salva", "remover", "web_on", "web_off", "limite"}:
        return HttpResponseBadRequest("Ação de integração inválida.")
    chave = request.POST.get("chave", "").strip() if acao in {"testar_nova", "salvar"} else ""
    atual = IntegracaoGemini.objects.filter(workspace=request.workspace).defer("credencial").first()
    try:
        if acao in {"testar_nova", "salvar"}:
            if not re.fullmatch(r"[\x21-\x7e]{1,4096}", chave):
                return _estado(request, f"Cole somente a chave copiada do {PROVEDORES[provedor_ativo()]['console_nome']}, sem espaços ou quebras de linha.")
            limite = atual.limite_diario if atual else limite_padrao()
            novo = provedor_ativo()
            cifra = criptografar(request.workspace.pk, chave) if acao == "salvar" else None
            testar_chave(novo, request.workspace.pk, chave, limite)
            if acao == "testar_nova":
                return _estado(request, "Teste aprovado. Para conectar, informe a chave novamente e escolha Testar e salvar.", True)
            _alterar(
                request, atual, f"{novo}_substituida" if atual else f"{novo}_conectada",
                provedor=novo, credencial=cifra, pesquisa_web=False, modelo_pesquisa="",
                limite_diario=limite, ultimo_teste=timezone.now(), status_teste="valida",
            )
            messages.success(request, f"{PROVEDORES[novo]['nome']} conectado para esta empresa.")
        elif not atual:
            return _estado(request, SEM_CHAVE)
        elif acao == "remover":
            with transaction.atomic():
                total, _ = IntegracaoGemini.objects.filter(
                    workspace=request.workspace, pk=atual.pk, versao=atual.versao,
                ).delete()
                if not total:
                    raise CredencialIndisponivel("A integração mudou. Atualize a página antes de remover.")
                _auditar(request, "removida")
            messages.success(request, "IA desconectada. Leads e relatórios foram preservados.")
        elif acao == "limite":
            raw = request.POST.get("limite", "")
            if not raw.isascii() or not raw.isdecimal() or len(raw) > 7 or int(raw) < 1 or int(raw) > limite_global():
                return _estado(request, "Informe um limite diário positivo que não ultrapasse o teto global do servidor.")
            _alterar(request, atual, "limite_alterado", limite_diario=int(raw))
            messages.success(request, "Limite diário atualizado.")
        elif atual.provedor not in PROVEDORES:
            return _estado(request, SEM_CHAVE)
        elif acao == "web_off":
            _alterar(request, atual, "busca_web_desativada", pesquisa_web=False, modelo_pesquisa="")
            messages.success(request, "Busca na web desativada.")
        elif acao == "web_on":
            if request.POST.get("autorizar_web") != "1":
                return _estado(request, "Confirme que a busca na web está autorizada para a conta de IA da empresa.")
            salvo = info(atual.provedor)
            if not salvo["busca_disponivel"]:
                return _estado(request, f"O modelo configurado não oferece a {salvo['busca_nome']}.")
            chave = descriptografar(atual)
            testar_chave(atual.provedor, request.workspace.pk, chave, atual.limite_diario, web=True)
            _alterar(request, atual, "busca_web_habilitada", pesquisa_web=True, modelo_pesquisa=salvo["modelo"])
            messages.success(request, "Busca na web testada e habilitada para esta empresa.")
        else:
            chave = descriptografar(atual)
            try:
                testar_chave(atual.provedor, request.workspace.pk, chave, atual.limite_diario)
            except IAIndisponivel as exc:
                IntegracaoGemini.objects.filter(pk=atual.pk, versao=atual.versao).update(
                    ultimo_teste=timezone.now(), status_teste=exc.codigo,
                )
                return _estado(request, str(exc))
            IntegracaoGemini.objects.filter(pk=atual.pk, versao=atual.versao).update(
                ultimo_teste=timezone.now(), status_teste="valida",
            )
            messages.success(request, f"A chave {info(atual.provedor)['nome']} salva tem acesso ao modelo configurado.")
    except (IAIndisponivel, CredencialIndisponivel) as exc:
        return _estado(request, str(exc))
    return redirect("integracao_groq")