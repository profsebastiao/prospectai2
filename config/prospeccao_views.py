import re
import hashlib
import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.access import scoped_queryset
from apps.accounts.models import Workspace
from apps.accounts.gemini_credentials import pode_gerenciar_ia, status_ia, web_habilitada
from apps.leads.ia import SEM_CHAVE, WEB_INDISPONIVEL
from apps.leads.models import AnaliseProspeccao, Lead, PesquisaProspeccao, RelatorioProspeccao
from apps.leads.perfis import instagram_handle, profile_identity
from apps.leads.diagnostico import diagnosticar, limpar_nome, nome_da_fonte
from apps.leads.prospeccao import (
    IAIndisponivel, analisar_candidato, analisar_ia, canais, coletar,
    consultas_oportunidades, fontes_confirmadas, identificar, normalizar, normalizar_fontes,
    ranquear, reportar, sugerir_consultas,
)
from apps.services.models import Servico
from config.busca_leads_views import BuscaLeadForm


CRITERIOS = {
    "ramo": "ramo_prospeccao",
    "publico": "publico_prospeccao",
    "regiao": "regiao_prospeccao",
}


PERFIL_OU_LINK = re.compile(r"^\s*@|https?://|www\.|^[\w.\-]*[._][\w.\-]*$", re.I)


class CriteriosForm(forms.Form):
    ramo = forms.CharField(
        label="Ramo de atuação da sua empresa", max_length=150,
        help_text="O que a sua empresa faz. Não é o nome nem o @ de um cliente.",
        widget=forms.TextInput(attrs={"placeholder": "Ex.: agência de marketing digital e desenvolvimento web"}),
    )
    publico = forms.CharField(
        label="Perfil de cliente que você quer encontrar", max_length=250,
        help_text="Descreva o tipo de negócio ideal, não um cliente específico.",
        widget=forms.TextInput(attrs={"placeholder": "Ex.: clínicas, salões e restaurantes com pouca presença digital"}),
    )
    regiao = forms.CharField(
        label="Região atendida", max_length=150,
        help_text="Quanto mais específica, melhores as buscas (cidade ou bairro).",
        widget=forms.TextInput(attrs={"placeholder": "Ex.: João Pessoa - PB"}),
    )
    servicos = forms.ModelMultipleChoiceField(
        label="Serviços do catálogo", queryset=Servico.objects.none(), widget=forms.CheckboxSelectMultiple,
        help_text="Dica: marque de 1 a 3 serviços para buscas mais focadas.",
    )
    salvar = forms.BooleanField(label="Salvar ramo, público e região no cadastro da empresa", required=False)

    def __init__(self, request, data=None, *, obrigatorio=True, inicial_salvo=None):
        ativos = scoped_queryset(request, Servico).filter(ativo=True)
        inicial = {nome: getattr(request.workspace, campo) for nome, campo in CRITERIOS.items()}
        inicial["servicos"] = list(ativos.values_list("pk", flat=True))
        if inicial_salvo:
            inicial.update({k: inicial_salvo.get(k, "") for k in CRITERIOS})
            inicial["servicos"] = [s["id"] for s in inicial_salvo["servicos"]]
        self.pode_salvar = request.user.has_perm("accounts.change_workspace")
        super().__init__(data, initial=inicial, prefix="criterios")
        if not self.pode_salvar:
            self.fields["salvar"].widget = forms.HiddenInput()
        self.fields["servicos"].queryset = ativos
        if not obrigatorio:
            for nome in (*CRITERIOS, "servicos"):
                self.fields[nome].required = False

    def _descricao(self, campo):
        valor = self.cleaned_data[campo]
        if valor and PERFIL_OU_LINK.search(valor):
            raise forms.ValidationError(
                "Isto parece um @perfil ou um link. Descreva com palavras (veja o exemplo). "
                "Para analisar um perfil específico, use Leads → Analisar um lead."
            )
        return valor

    def clean_ramo(self):
        return self._descricao("ramo")

    def clean_publico(self):
        return self._descricao("publico")

    def clean_regiao(self):
        return self._descricao("regiao")

    def clean_salvar(self):
        salvar = self.cleaned_data["salvar"]
        if salvar and not self.pode_salvar:
            raise forms.ValidationError("Você não tem permissão para alterar o cadastro da empresa.")
        return salvar

    def criterios(self, request):
        valores = {nome: self.cleaned_data[nome] for nome in CRITERIOS}
        if self.cleaned_data["salvar"]:
            Workspace.objects.filter(pk=request.workspace.pk).update(
                **{campo: valores[nome] for nome, campo in CRITERIOS.items()}
            )
        return {
            "empresa": request.workspace.nome, **valores,
            "servicos": [
                {"id": s.pk, "nome": s.nome}
                for s in self.cleaned_data["servicos"]
            ],
        }


class IdentidadeForm(BuscaLeadForm):
    destinatario = forms.CharField(
        label="Destinatário do relatório (opcional)", max_length=100, required=False,
        help_text="Exemplo: proprietário do negócio ou responsável pelo comercial.",
    )


class EvidenciaForm(forms.Form):
    content = forms.CharField(
        label="Trecho público encontrado", max_length=2100, required=False,
        help_text="Cole o texto que você leu na página (post, anúncio, avaliação, descrição do perfil).",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Ex.: \"Procuro alguém para criar o site da minha clínica em João Pessoa...\""}),
    )
    title = forms.CharField(
        label="Nome da fonte (opcional)", max_length=200, required=False,
        widget=forms.TextInput(attrs={"placeholder": "Ex.: Instagram da Clínica Sorriso"}),
    )
    url = forms.URLField(label="Link público (opcional)", max_length=500, required=False, assume_scheme="https")
    published_date = forms.DateField(
        label="Data da publicação (opcional)", required=False,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )

    def clean_content(self):
        texto = self.cleaned_data["content"].strip()
        links = re.findall(r"https?://\S+", texto)
        palavras = re.sub(r"https?://\S+", " ", texto).split()
        # Uma lista de links (ex.: botões do Linktree) é evidência dos canais da empresa.
        so_um_nome = not links and (len(palavras) < 4 or PERFIL_OU_LINK.search(texto) and len(palavras) < 6)
        um_link_solto = len(links) == 1 and len(palavras) < 3
        if texto and (so_um_nome or um_link_solto):
            raise forms.ValidationError(
                "Isto parece só um nome, @perfil ou link. Cole o texto que aparece na página "
                "(a bio do perfil, um post, um anúncio ou uma avaliação), com pelo menos uma frase."
            )
        return texto

    def clean_url(self):
        url = self.cleaned_data["url"]
        if url and not url.lower().startswith(("https://", "http://")):
            raise forms.ValidationError("Use um link HTTP ou HTTPS.")
        return url

    def clean(self):
        dados = super().clean()
        if any(dados.get(campo) for campo in ("title", "url", "published_date")) and not dados.get("content"):
            self.add_error("content", "Cole o conteúdo da fonte. Um link sozinho não foi consultado.")
        return dados


Evidencias = forms.formset_factory(EvidenciaForm, extra=5, max_num=5, validate_max=True, absolute_max=5)


def _chave(*partes):
    raw = json.dumps(partes, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _evidencias_manuais(formset):
    return [
        {
            "title": dados["title"], "url": dados["url"], "content": dados["content"],
            "published_date": dados["published_date"].isoformat() if dados["published_date"] else "",
            "origem": "manual",
        }
        for dados in formset.cleaned_data if dados.get("content")
    ]


class RelatorioForm(forms.ModelForm):
    revisao = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    class Meta:
        model = RelatorioProspeccao
        fields = ["titulo", "achados", "recomendacoes", "observacoes_internas", "revisao"]
        labels = {
            "recomendacoes": "Recomendações para compartilhar",
            "observacoes_internas": "Observações internas, excluídas do download",
        }
        widgets = {
            "achados": forms.Textarea(attrs={"rows": 8}),
            "recomendacoes": forms.Textarea(attrs={"rows": 6}),
            "observacoes_internas": forms.Textarea(attrs={"rows": 3}),
        }


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def iniciar(request, modo):
    if modo not in PesquisaProspeccao.Modo.values:
        return HttpResponseBadRequest("Modo de prospecção inválido.")
    oportunidades = modo == PesquisaProspeccao.Modo.OPORTUNIDADES
    data = request.POST if request.method == "POST" else None
    anterior = None
    repetir = request.GET.get("repetir")
    if repetir:
        if not repetir.isascii() or not repetir.isdecimal() or len(repetir) > 18:
            return HttpResponseBadRequest("Pesquisa inválida.")
        anterior = get_object_or_404(
            scoped_queryset(request, PesquisaProspeccao), pk=repetir, modo=modo,
        )
    criterios = CriteriosForm(
        request, data, obrigatorio=oportunidades,
        inicial_salvo=anterior.criterios if anterior else None,
    )
    evidencias = Evidencias(data, prefix="evidencias", initial=[
        {campo: fonte.get(campo, "") for campo in ("title", "url", "content")}
        | {"published_date": fonte.get("publicacao", "")}
        for fonte in (anterior.fontes if anterior else []) if fonte.get("origem") == "manual"
    ])
    identidade = None
    if not oportunidades:
        inicial = {}
        if anterior:
            inicial = {
                "consulta": anterior.consulta, "rede": anterior.criterios.get("rede", ""),
                "cidade": anterior.criterios.get("cidade", ""),
                "destinatario": anterior.criterios.get("destinatario", ""),
            }
        if request.GET.get("lead") is not None:
            pk = request.GET["lead"]
            if not pk.isascii() or not pk.isdecimal() or len(pk) > 18:
                return HttpResponseBadRequest("Lead inválido.")
            lead = get_object_or_404(scoped_queryset(request, Lead), pk=pk)
            usuario = lead.usuario_social or instagram_handle(lead.instagram)
            inicial = {
                "consulta": lead.perfil_url or (f"@{usuario}" if usuario else lead.empresa or lead.nome),
                "rede": lead.rede_social or ("instagram" if usuario else ""),
                "cidade": lead.cidade,
            }
        identidade = IdentidadeForm(data, initial=inicial)

    sugestoes = []
    aviso = ""
    config_ia = status_ia(request.workspace.pk)
    usa_web = web_habilitada(config_ia)

    def pagina():
        return render(request, "prospeccao_inicio.html", {
            "oportunidades": oportunidades, "criterios": criterios, "identidade": identidade,
            "sem_servicos": not criterios.fields["servicos"].queryset.exists(),
            "evidencias": evidencias, "sugestoes": sugestoes, "aviso": aviso,
            "web_habilitada": usa_web, "ia_conectada": config_ia is not None,
            "pode_gerenciar_ia": pode_gerenciar_ia(request.user, request.workspace),
        })
    if data is not None:
        acao = request.POST.get("acao", "manual")
        if acao not in {"sugerir", "manual", "web"} or (acao == "sugerir" and not oportunidades):
            return HttpResponseBadRequest("Ação de prospecção inválida.")
        criterios_validos = criterios.is_valid()
        identidade_valida = identidade is None or identidade.is_valid()
        if criterios_validos and identidade_valida:
            if acao == "sugerir":
                contexto = criterios.criterios(request)
                try:
                    sugestoes = sugerir_consultas(contexto, request.workspace.pk)
                except IAIndisponivel as exc:
                    aviso = str(exc)
            elif acao == "web" and not usa_web:
                aviso = SEM_CHAVE if not config_ia else WEB_INDISPONIVEL
            else:
                if acao == "manual":
                    if not evidencias.is_valid():
                        itens = []
                        com_erro = [str(i + 1) for i, form in enumerate(evidencias.forms) if form.errors]
                        aviso = (
                            f"Corrija a evidência {', '.join(com_erro)} (marcada em vermelho abaixo) e clique em analisar de novo."
                            if com_erro else "Revise as evidências abaixo e clique em analisar de novo."
                        )
                    else:
                        itens = _evidencias_manuais(evidencias)
                        if not itens:
                            aviso = "Cole pelo menos um trecho público. Informar apenas nome, @perfil ou link não consulta a fonte."
                else:
                    itens = []
                if not aviso:
                    contexto = criterios.criterios(request)
                    if oportunidades:
                        consulta = contexto["regiao"]
                        consultas = consultas_oportunidades(contexto)
                        cidade = contexto["regiao"]
                    else:
                        consulta = identidade.cleaned_data["consulta"]
                        rede = identidade.cleaned_data["rede"]
                        cidade = identidade.cleaned_data["cidade"]
                        contexto.update(destinatario=identidade.cleaned_data["destinatario"], cidade=cidade, rede=rede)
                        alvo = " ".join(x for x in (consulta, rede, cidade) if x)
                        consultas = (alvo + " perfil negócio", alvo + " site oficial contato Google Maps")

                    dia = timezone.localdate().isoformat() if acao == "web" else ""
                    chave = _chave(modo, consulta, contexto, acao, itens, dia)
                    existentes = scoped_queryset(request, PesquisaProspeccao).filter(chave=chave)
                    registro = existentes.first()
                    if registro and not registro.avisos:
                        messages.info(
                            request,
                            "Esta análise já tinha sido feita com os mesmos critérios e evidências. "
                            "Mostrando o resultado anterior; mude ou acrescente evidências para uma nova análise.",
                        )
                        return redirect("prospeccao_pesquisa", pk=registro.pk)
                    fontes, avisos, widget = (
                        coletar(consultas, request.workspace.pk) if acao == "web"
                        else (normalizar_fontes(itens), [], "")
                    )
                    if acao == "web" and not fontes and any(
                        request.POST.get(f"{evidencias.prefix}-{i}-{campo}")
                        for i in range(evidencias.max_num)
                        for campo in ("title", "url", "published_date", "content")
                    ):
                        aviso = " ".join(avisos) or "A pesquisa não retornou referências públicas."
                        return pagina()
                    if widget:
                        contexto["widget_google"] = widget
                    resultados = []
                    if fontes:
                        if oportunidades:
                            try:
                                dados = analisar_ia("oportunidades", contexto, fontes, workspace_id=request.workspace.pk)
                                resultados = ranquear(dados, fontes, contexto)
                            except IAIndisponivel as exc:
                                avisos.append(str(exc))
                        else:
                            resultados = identificar(fontes, cidade, consulta)
                    valores = {
                        "modo": modo, "consulta": consulta, "criterios": contexto,
                        "fontes": fontes, "avisos": avisos, "resultados": resultados,
                    }
                    if registro:
                        # A successful retry from another request must win over a failure.
                        existentes.exclude(avisos=[]).update(**valores)
                    else:
                        registro, _ = PesquisaProspeccao.objects.get_or_create(
                            workspace=request.workspace, chave=chave, defaults=valores,
                        )
                    return redirect("prospeccao_pesquisa", pk=registro.pk)

    return pagina()


def _indice(valor, tamanho):
    if not valor.isascii() or not valor.isdecimal() or len(valor) > 3:
        return None
    indice = int(valor)
    return indice if indice < tamanho else None


def _lead_oportunidade(resultado, workspace):
    fonte = resultado["fonte"]
    nome = resultado["nome"][:200]
    cidade = resultado["regiao"][:120]
    perfil = profile_identity(fonte["url"])
    assinatura = _chave(normalizar(nome), normalizar(fonte["content"]), fonte["url"])
    existentes = Lead.objects.filter(workspace=workspace)
    def ja_cadastrado():
        return existentes.filter(chave_oportunidade=assinatura).exists() or (
            bool(fonte["url"]) and existentes.filter(fonte_prospeccao=fonte["url"]).exists()
        )

    if ja_cadastrado():
        return False
    if cidade and (
        existentes.filter(cidade__iexact=cidade, nome__iexact=nome).exists()
        or existentes.filter(cidade__iexact=cidade, empresa__iexact=nome).exists()
    ):
        return False
    lead = Lead(
        workspace=workspace, nome=nome, cidade=cidade, origem="Prospecção com IA",
        fonte_prospeccao=fonte["url"], chave_oportunidade=assinatura,
        observacoes=(
            f'{resultado["classificacao"]} · {resultado["servico"]}\n'
            f'{resultado["texto"]}\nEvidência: {resultado["evidencia"]}\nFonte: {fonte["url"] or "Trecho fornecido, sem link"}'
        ),
    )
    if perfil:
        lead.rede_social, lead.usuario_social, lead.perfil_url = perfil
    try:
        with transaction.atomic():
            lead.full_clean()
            lead.save()
    except ValidationError:
        if lead.duplicate_profile_field() or ja_cadastrado():
            return False
        raise
    except IntegrityError:
        if lead.duplicate_profile_field() or ja_cadastrado():
            return False
        raise
    return True


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def pesquisa(request, pk):
    registro = get_object_or_404(scoped_queryset(request, PesquisaProspeccao), pk=pk)
    oportunidades = registro.modo == PesquisaProspeccao.Modo.OPORTUNIDADES
    if request.method == "POST":
        if oportunidades and "relatorio" in request.POST:
            return _relatorio_oportunidade(request, registro)
        if oportunidades:
            indices = {
                _indice(valor, len(registro.resultados)) for valor in request.POST.getlist("selecionados")
            }
            if not indices or None in indices or any(
                registro.resultados[i]["tipo"] == "insuficiente" for i in indices
            ):
                messages.error(request, "Selecione as oportunidades que deseja cadastrar.")
            else:
                salvos = sum(
                    _lead_oportunidade(registro.resultados[i], request.workspace) for i in sorted(indices)
                )
                messages.success(request, f"Leads cadastrados: {salvos}. Possíveis duplicados: {len(indices) - salvos}.")
            return redirect("prospeccao_pesquisa", pk=pk)

        indice = _indice(request.POST.get("candidato", ""), len(registro.resultados))
        if indice is None:
            return HttpResponseBadRequest("Selecione um candidato desta pesquisa.")
        candidato = registro.resultados[indice]
        destinatario = registro.criterios.get("destinatario", "")
        chave = _chave(indice)
        anteriores = scoped_queryset(request, AnaliseProspeccao).filter(pesquisa=registro, chave=chave)
        existente = anteriores.first()
        if existente and not existente.dados.get("aviso"):
            return redirect("prospeccao_analise", pk=existente.pk)
        try:
            dados = analisar_candidato(
                candidato, registro.fontes, registro.criterios, destinatario, workspace_id=request.workspace.pk
            )
        except IAIndisponivel as exc:
            fontes = fontes_confirmadas(candidato, registro.fontes)
            dados = {
                "aviso": str(exc), "fontes": fontes, "canais": canais(candidato, fontes),
                "diagnostico": diagnosticar(
                    candidato, fontes, registro.criterios.get("servicos"), registro.criterios.get("cidade", ""),
                ),
                "achados": [], "recomendacoes": [],
                "limitacao": "A análise está limitada às evidências selecionadas; links informados não foram abertos.",
            }
        if existente:
            if not dados.get("aviso"):
                anteriores.update(dados=dados)
            analise = existente
        else:
            analise, _ = AnaliseProspeccao.objects.get_or_create(
                pesquisa=registro, chave=chave,
                defaults={
                    "candidato": indice, "dados": dados, "destinatario": destinatario,
                    "nome_lead": (candidato["nome"] or candidato["titulo"])[:200],
                },
            )
        return redirect("prospeccao_analise", pk=analise.pk)

    return render(request, "prospeccao_pesquisa.html", {
        "pesquisa": registro, "oportunidades": oportunidades,
        "analises": scoped_queryset(request, AnaliseProspeccao).filter(pesquisa=registro).order_by("-pk"),
    })


PREFIXOS_NOME = re.compile(
    r"^(?:perfil|instagram|facebook|site|p[aá]gina|post(?:s)?(?: do instagram)?(?: de promo[cç][aã]o)?)\s+(?:d[aeo]s?\s+)?",
    re.I,
)


def _nome_empresa(fontes):
    for fonte in fontes:
        titulo = (fonte.get("title") or "").strip()
        if titulo:
            return limpar_nome(PREFIXOS_NOME.sub("", titulo).strip(" –-:") or titulo)
    return nome_da_fonte(fontes[0], "Empresa pesquisada") if fontes else "Empresa pesquisada"


def _relatorio_todas(request, registro):
    """Um único relatório usando todas as evidências da consulta (mesma empresa)."""
    fontes = [f for f in registro.fontes if not f.get("restrita")]
    if not fontes:
        return HttpResponseBadRequest("Esta consulta não tem evidências.")
    nome = _nome_empresa(fontes)
    cidade = registro.criterios.get("regiao", "")
    candidato = {
        "fonte": next((f["url"] for f in fontes if f["url"]), ""), "nome": nome, "titulo": nome,
        "descricao": "\n".join(f["content"] for f in fontes), "cidade": cidade,
        "perfis": [], "principal": None, "links": [], "email": "", "telefone": "", "whatsapp": "",
    }
    chave = _chave("relatorio-todas", len(fontes))
    anteriores = scoped_queryset(request, AnaliseProspeccao).filter(pesquisa=registro, chave=chave)
    existente = anteriores.first()
    if existente and not existente.dados.get("aviso"):
        return redirect("prospeccao_analise", pk=existente.pk)
    criterios = {**registro.criterios, "cidade": cidade}
    try:
        dados = analisar_candidato(
            candidato, registro.fontes, criterios, "", workspace_id=request.workspace.pk, todas=True,
        )
    except IAIndisponivel as exc:
        dados = {
            "aviso": f"{exc} O diagnóstico de presença digital abaixo foi feito sem a IA.",
            "fontes": fontes, "canais": canais(candidato, fontes),
            "diagnostico": diagnosticar(candidato, fontes, registro.criterios.get("servicos"), cidade),
            "achados": [], "recomendacoes": [],
            "limitacao": "A análise está limitada às evidências fornecidas; links informados não foram abertos.",
        }
    if existente:
        anteriores.update(dados=dados)
        analise = existente
    else:
        analise, _ = AnaliseProspeccao.objects.get_or_create(
            pesquisa=registro, chave=chave,
            defaults={"candidato": 0, "dados": dados, "nome_lead": nome[:200]},
        )
    return redirect("prospeccao_analise", pk=analise.pk)


def _relatorio_oportunidade(request, registro):
    """Gera (ou reabre) o diagnóstico e o relatório da empresa de uma oportunidade."""
    if request.POST.get("relatorio") == "todas":
        return _relatorio_todas(request, registro)
    indice = _indice(request.POST.get("relatorio", ""), len(registro.resultados))
    if indice is None:
        return HttpResponseBadRequest("Selecione uma oportunidade desta pesquisa.")
    resultado = registro.resultados[indice]
    fonte = resultado["fonte"]
    nome = limpar_nome(resultado.get("nome") or nome_da_fonte(fonte, "Empresa pesquisada"))
    cidade = resultado.get("regiao") or registro.criterios.get("regiao", "")
    candidato = next(iter(identificar([fonte], cidade, nome)), None) or {
        "fonte": "", "fonte_id": fonte["id"], "nome": nome, "titulo": nome, "descricao": fonte["content"],
        "cidade": cidade, "perfis": [], "principal": None, "links": [], "email": "", "telefone": "", "whatsapp": "",
    }
    candidato["nome"] = nome
    candidato.setdefault("descricao", fonte["content"])
    if not candidato.get("fonte_id") and not fonte["url"]:
        candidato["fonte_id"] = fonte["id"]
    chave = _chave("relatorio", indice)
    anteriores = scoped_queryset(request, AnaliseProspeccao).filter(pesquisa=registro, chave=chave)
    existente = anteriores.first()
    if existente and not existente.dados.get("aviso"):
        return redirect("prospeccao_analise", pk=existente.pk)
    criterios = {**registro.criterios, "cidade": cidade}
    try:
        dados = analisar_candidato(candidato, registro.fontes, criterios, "", workspace_id=request.workspace.pk)
    except IAIndisponivel as exc:
        fontes = fontes_confirmadas(candidato, registro.fontes)
        dados = {
            "aviso": f"{exc} O diagnóstico de presença digital abaixo foi feito sem a IA.",
            "fontes": fontes, "canais": canais(candidato, fontes),
            "diagnostico": diagnosticar(candidato, fontes, registro.criterios.get("servicos"), cidade),
            "achados": [], "recomendacoes": [],
            "limitacao": "A análise está limitada às evidências selecionadas; links informados não foram abertos.",
        }
    if existente:
        anteriores.update(dados=dados)
        analise = existente
    else:
        analise, _ = AnaliseProspeccao.objects.get_or_create(
            pesquisa=registro, chave=chave,
            defaults={"candidato": indice, "dados": dados, "nome_lead": nome[:200]},
        )
    return redirect("prospeccao_analise", pk=analise.pk)


@login_required
@never_cache
@require_http_methods(["GET"])
def historico(request):
    pesquisas = scoped_queryset(request, PesquisaProspeccao)
    pagina = Paginator(pesquisas, 20).get_page(request.GET.get("page"))
    return render(request, "prospeccao_historico.html", {"pagina": pagina})


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def analise(request, pk):
    registro = get_object_or_404(
        scoped_queryset(request, AnaliseProspeccao).select_related("pesquisa"), pk=pk,
    )
    if request.method == "POST":
        relatorio, _ = RelatorioProspeccao.objects.get_or_create(
            analise=registro,
            defaults={"empresa_nome": request.workspace.nome, **reportar(registro)},
        )
        return redirect("prospeccao_relatorio", pk=relatorio.pk)
    return render(request, "prospeccao_analise.html", {"analise": registro})


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def relatorio(request, pk):
    registro = get_object_or_404(
        scoped_queryset(request, RelatorioProspeccao).select_related("analise"), pk=pk,
    )
    form = RelatorioForm(request.POST if request.method == "POST" else None, instance=registro)
    if request.method == "POST" and form.is_valid():
        campos = form.cleaned_data
        aprovando = request.POST.get("acao") == "aprovar"
        atualizado = scoped_queryset(request, RelatorioProspeccao).filter(
            pk=pk, revisao=campos["revisao"],
        ).update(
            titulo=campos["titulo"], achados=campos["achados"],
            recomendacoes=campos["recomendacoes"], observacoes_internas=campos["observacoes_internas"],
            aprovado_em=timezone.now() if aprovando else None, revisao=campos["revisao"] + 1,
        )
        if not atualizado:
            form.add_error(None, "O relatório mudou em outra aba. Reabra a versão atual antes de salvar.")
            return render(request, "prospeccao_relatorio.html", {"relatorio": registro, "form": form}, status=409)
        messages.success(request, "Versão aprovada para download." if aprovando else "Rascunho salvo.")
        return redirect("prospeccao_relatorio", pk=pk)
    return render(request, "prospeccao_relatorio.html", {"relatorio": registro, "form": form})


@login_required
@never_cache
@require_http_methods(["GET"])
def exportar(request, pk):
    registro = get_object_or_404(
        scoped_queryset(request, RelatorioProspeccao).select_related("analise"), pk=pk,
    )
    if not registro.aprovado_em:
        return HttpResponseForbidden("Aprove a versão atual antes de baixar.")
    response = render(request, "prospeccao_exportacao.html", {
        "titulo": registro.titulo, "empresa": registro.empresa_nome,
        "lead": registro.analise.nome_lead, "data_analise": registro.analise.criado_em,
        "destinatario": registro.analise.destinatario,
        "achados": registro.achados, "recomendacoes": registro.recomendacoes,
    })
    response["Content-Disposition"] = f'attachment; filename="relatorio-prospectai-{registro.pk}.html"'
    return response
