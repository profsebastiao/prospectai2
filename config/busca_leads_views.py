from django import forms
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.access import scope_form
from apps.accounts.gemini_credentials import pode_gerenciar_groq, status_groq
from apps.leads.busca_web import BuscaIndisponivel, buscar, candidatos
from apps.leads.models import Lead
from apps.leads.perfis import RedeSocial, handle, profile_identity, web_url
from config.gestao_views import criar_formulario


SALT = "prospectai.leads.busca-web"


class BuscaLeadForm(forms.Form):
    consulta = forms.CharField(
        label="@usuário, link do perfil ou nome do negócio", max_length=250,
        widget=forms.TextInput(attrs={"placeholder": "@negocio, link ou nome"}),
    )
    rede = forms.ChoiceField(
        label="Rede social", choices=[("", "Qualquer rede"), *RedeSocial.choices], required=False
    )
    cidade = forms.CharField(label="Cidade (opcional)", max_length=120, required=False)

    def clean_consulta(self):
        consulta = self.cleaned_data["consulta"]
        if consulta.startswith("@") and not handle(consulta):
            raise forms.ValidationError("Informe um @usuário válido ou o nome do negócio.")
        return consulta


def _identidade(consulta):
    if "://" not in consulta and "/" in consulta:
        consulta = "https://" + consulta
    return profile_identity(consulta)


def _initial_manual(dados):
    consulta = dados["consulta"]
    identidade = _identidade(consulta)
    _, usuario, _ = identidade or ("", handle(consulta) if consulta.startswith("@") else "", "")
    nome = "" if identidade or "://" in consulta else (usuario or consulta)
    inicial = {"nome": nome[:200], "cidade": dados["cidade"], "origem": "Busca na internet"}
    if identidade:
        inicial.update(zip(("rede_social", "usuario_social", "perfil_url"), identidade))
    elif dados["rede"]:
        inicial.update(rede_social=dados["rede"], usuario_social=usuario)
    if not identidade and "://" in consulta and (url := web_url(consulta)):
        inicial["perfil_url"] = url
    return inicial


def _initial_candidato(candidato, consultado):
    inicial = {
        campo: candidato[campo] for campo in ("nome", "segmento", "cidade", "email", "telefone", "whatsapp")
    }
    inicial["origem"] = "Busca na internet"
    principal = candidato["principal"]
    if principal:
        inicial.update(rede_social=principal["rede"], usuario_social=principal["usuario"], perfil_url=principal["url"])
    site = next((link["url"] for link in candidato["links"] if link["tipo"] == "Site do negócio"), "")
    if len(site) <= 200:
        inicial["site"] = site
    notas = [f"Fonte: {candidato['fonte']}\nConsultada em {consultado:%d/%m/%Y}."]
    if candidato["descricao"]:
        notas.append(candidato["descricao"][:650])
    notas.extend(
        f"{p['nome']}: {p['url']} — {p['evidencia']} Fonte: {p['fonte']}"
        for p in (candidato["perfis"][:6] if principal else ())
    )
    notas.extend(
        f"{link['tipo']}: {link['url']} — {link['evidencia']} Fonte: {link['fonte']}"
        for link in candidato["links"][:8]
    )
    inicial["observacoes"] = "\n\n".join(notas)
    return inicial


def _token(request, inicial):
    return signing.dumps({
        "empresa": request.workspace.pk,
        "usuario": request.user.pk,
        "inicial": inicial,
    }, salt=SALT, compress=True)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def buscar_leads_web(request):
    if request.method == "POST" and request.POST.get("acao") == "revisar":
        try:
            dados = signing.loads(request.POST.get("resultado", ""), salt=SALT, max_age=1800)
        except signing.BadSignature:
            return HttpResponseBadRequest("Seleção inválida ou expirada. Faça uma nova busca.")
        if dados["empresa"] != request.workspace.pk or dados["usuario"] != request.user.pk:
            return HttpResponseBadRequest("Esta seleção não pertence à sessão e à empresa ativas.")
        formulario = scope_form(request, criar_formulario(Lead)(initial=dados["inicial"]))
        return render(request, "gestao_form.html", {
            "titulo": "Revisar e cadastrar lead", "secao": "leads", "secao_titulo": "Leads",
            "formulario": formulario, "form_action": reverse("gestao_novo", args=["leads"]),
        })

    form = BuscaLeadForm(request.POST if request.method == "POST" else None)
    context = {
        "form": form,
        "groq_conectado": status_groq(request.workspace.pk) is not None,
        "pode_gerenciar_groq": pode_gerenciar_groq(request.user, request.workspace),
    }
    if form.is_bound and form.is_valid():
        dados = form.cleaned_data
        consulta = dados["consulta"]
        identidade = _identidade(consulta)
        rede = dados["rede"] or (identidade[0].value if identidade else "")
        query = " ".join(part for part in (
            consulta, RedeSocial(rede).label if rede else "", dados["cidade"], "negócio"
        ) if part)
        context["manual"] = _token(request, _initial_manual(dados))
        try:
            encontrados = buscar(query, workspace_id=request.workspace.pk)
            resultados = candidatos(encontrados, dados["cidade"])
            context["widget_google"] = next(
                (item["widget_google"] for item in encontrados if isinstance(item, dict) and "widget_google" in item), ""
            )
        except BuscaIndisponivel as exc:
            context["erro"] = str(exc)
        else:
            consultado = timezone.localtime()
            for candidato in resultados:
                candidato["selecao"] = _token(request, _initial_candidato(candidato, consultado))
                candidato["site_encontrado"] = any(
                    link["tipo"] == "Site do negócio" for link in candidato["links"]
                )
                candidato["landing_encontrada"] = any(
                    link["tipo"] == "Landing page" for link in candidato["links"]
                )
            context.update(resultados=resultados, consultado=consultado, consultou=True)
    return render(request, "buscar_leads_web.html", context)
