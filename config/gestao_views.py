from django import forms
from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import CharField, Count, Q, TextField
from django.forms import modelform_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.access import pode_editar, scope_form, scoped_queryset
from apps.leads.models import LEAD_STAGES_CLOSED, Lead
from apps.projects.models import Projeto
from apps.tasks.models import Tarefa

SECOES = {
    "leads": ("leads", "Lead", "Leads"),
    "servicos": ("services", "Servico", "Serviços"),
    "projetos": ("projects", "Projeto", "Projetos"),
    "tarefas": ("tasks", "Tarefa", "Tarefas"),
    "modelos-de-tarefa": ("services", "ModeloTarefa", "Modelos de tarefa"),
    "contratos": ("finance", "Contrato", "Contratos"),
    "cobrancas": ("finance", "Cobranca", "Cobranças"),
    "pagamentos": ("finance", "Pagamento", "Pagamentos"),
}


ACOES_NOVO = {
    "leads": "Novo lead",
    "servicos": "Novo serviço",
    "projetos": "Novo projeto",
    "tarefas": "Nova tarefa",
    "modelos-de-tarefa": "Novo modelo de tarefa",
    "contratos": "Novo contrato",
    "cobrancas": "Nova cobrança",
    "pagamentos": "Novo pagamento",
}

STATUS_CLASSES = {
    "andamento": "status-progress",
    "contato": "status-progress",
    "negociacao": "status-progress",
    "perdido": "status-urgent",
    "revisao": "status-progress",
    "convertido": "status-positive",
    "concluido": "status-positive",
    "concluida": "status-positive",
    "pausado": "status-today",
    "aguardando": "status-today",
    "cancelada": "status-urgent",
}


class RegistrarContatoForm(forms.Form):
    proximo_retorno = forms.DateField(
        label="Próximo retorno", required=False,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        help_text="Deixe em branco se não houver um próximo retorno.",
    )

    def clean_proximo_retorno(self):
        retorno = self.cleaned_data["proximo_retorno"]
        if retorno and retorno < timezone.localdate():
            raise forms.ValidationError("Escolha hoje ou uma data futura.")
        return retorno


def configuracao(secao):
    if secao not in SECOES:
        raise Http404("Página não encontrada")
    app, nome, titulo = SECOES[secao]
    return apps.get_model(app, nome), titulo


def criar_formulario(modelo):
    campos = [
        f.name for f in modelo._meta.fields
        if f.editable and not f.primary_key
    ]
    if modelo is Lead:
        widgets = {
            campo: forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
            for campo in ("ultimo_contato", "proximo_retorno")
        }
        return modelform_factory(modelo, fields=campos, widgets=widgets)
    return modelform_factory(modelo, fields=campos)


@login_required
def gestao_lista(request, secao):
    modelo, titulo = configuracao(secao)

    busca = request.GET.get("q", "").strip()[:100]
    registros = scoped_queryset(request, modelo).order_by("-pk")

    campos_pesquisa = [
        f.name for f in modelo._meta.fields
        if isinstance(f, (CharField, TextField))
    ]

    if busca and campos_pesquisa:
        condicao = Q()
        for campo in campos_pesquisa:
            condicao |= Q(**{f"{campo}__icontains": busca})
        registros = registros.filter(condicao)

    task_filters = ()
    lead_stages = ()
    retorno_filtro = ""
    active_filter = ""
    if secao == "tarefas":
        today = timezone.localdate()
        task_filters = (
            ("", "Todas"),
            ("pendentes", "Pendentes"),
            ("atrasadas", "Atrasadas"),
            ("hoje", "Vencem hoje"),
            ("minhas", "Minhas tarefas"),
            ("minhas-hoje", "Minhas de hoje"),
        )
        active_filter = request.GET.get("filtro", "")
        if active_filter in {"atrasadas", "hoje", "minhas-hoje"}:
            due = Q(prazo__lt=today) if active_filter == "atrasadas" else Q(prazo=today)
            registros = registros.filter(due).exclude(status=Tarefa.Status.CONCLUIDA)
            if active_filter == "minhas-hoje":
                registros = registros.filter(responsavel=request.user)
            registros = registros.order_by("prazo", "pk")
        elif active_filter == "pendentes":
            registros = registros.exclude(status=Tarefa.Status.CONCLUIDA)
        elif active_filter == "minhas":
            registros = registros.filter(responsavel=request.user)
        else:
            active_filter = ""
        registros = registros.select_related("projeto", "responsavel")
        member_ids = set(request.workspace.usuarios.values_list("pk", flat=True))
    elif secao == "leads":
        lead_stages = Lead.Status.choices
        active_filter = request.GET.get("filtro", "")
        if active_filter in Lead.Status.values:
            registros = registros.filter(status=active_filter)
        else:
            active_filter = ""
        hoje = timezone.localdate()
        retorno_filtro = request.GET.get("retorno", "")
        if retorno_filtro in {"hoje", "atrasados"}:
            periodo = Q(proximo_retorno=hoje) if retorno_filtro == "hoje" else Q(proximo_retorno__lt=hoje)
            registros = registros.filter(periodo).exclude(status__in=LEAD_STAGES_CLOSED)
            registros = registros.order_by("proximo_retorno", "pk")
        else:
            retorno_filtro = ""
    elif secao == "projetos":
        registros = registros.select_related("cliente").annotate(
            total_tarefas=Count("tarefas"),
            tarefas_concluidas=Count(
                "tarefas", filter=Q(tarefas__status=Tarefa.Status.CONCLUIDA)
            ),
        )

    pagina = Paginator(registros, 15).get_page(
        request.GET.get("page")
    )

    resumo = []
    for registro in pagina:
        nome = (
            getattr(registro, "nome", None)
            or getattr(registro, "titulo", None)
            or str(registro)
        )
        item = {
            "pk": registro.pk,
            "nome": str(nome),
            "empresa": getattr(registro, "empresa", ""),
            "status": getattr(registro, "get_status_display", lambda: "")(),
            "status_class": STATUS_CLASSES.get(getattr(registro, "status", "")),
            "prioridade": getattr(registro, "get_prioridade_display", lambda: "")(),
            "prazo": getattr(registro, "prazo", None) or getattr(registro, "prazo_entrega", None),
        }
        if secao == "tarefas":
            item["projeto"] = registro.projeto.nome
            item["responsavel"] = (
                registro.responsavel.get_full_name() or registro.responsavel.get_username()
                if registro.responsavel_id in member_ids else ""
            )
        elif secao == "projetos":
            item.update(
                cliente=registro.cliente.nome,
                total_tarefas=registro.total_tarefas,
                tarefas_concluidas=registro.tarefas_concluidas,
                progresso=round(100 * registro.tarefas_concluidas / registro.total_tarefas)
                if registro.total_tarefas else 0,
            )
        elif secao == "leads":
            item["email"] = registro.email
            item["telefone"] = registro.whatsapp or registro.telefone
            item["ultimo_contato"] = registro.ultimo_contato
            item["proximo_retorno"] = registro.proximo_retorno
            if registro.proximo_retorno and registro.status not in LEAD_STAGES_CLOSED:
                item["retorno_atrasado"] = registro.proximo_retorno < hoje
                item["retorno_hoje"] = registro.proximo_retorno == hoje
        resumo.append(item)

    return render(request, "gestao_lista.html", {
        "secao": secao,
        "titulo": titulo,
        "registros": resumo,
        "acao_novo": ACOES_NOVO[secao],
        "pagina": pagina,
        "busca": busca,
        "filtro": active_filter,
        "task_filters": task_filters,
        "lead_stages": lead_stages,
        "retorno_filtro": retorno_filtro,
        "pode_criar": pode_editar(request),
        "pode_editar": pode_editar(request),
    })


def salvar_formulario(formulario):
    if not isinstance(formulario.instance, Lead):
        try:
            formulario.save()
        except ValidationError as erro:
            formulario.add_error(None, erro)
            return False
        return True
    try:
        with transaction.atomic():
            formulario.save()
    except IntegrityError:
        campo = formulario.instance.duplicate_profile_field()
        if not campo:
            raise
        formulario.add_error(campo, "Este perfil já está cadastrado nesta empresa.")
        return False
    return True


@login_required
def gestao_novo(request, secao):
    modelo, titulo = configuracao(secao)

    projeto = None
    initial = {}
    if secao == "tarefas" and "projeto" in request.GET:
        projeto_pk = request.GET["projeto"]
        if not projeto_pk.isascii() or not projeto_pk.isdecimal() or len(projeto_pk) > 19:
            raise Http404
        projeto = get_object_or_404(scoped_queryset(request, Projeto), pk=projeto_pk)
        initial["projeto"] = projeto.pk

    Formulario = criar_formulario(modelo)
    formulario = scope_form(request, Formulario(request.POST or None, initial=initial))
    if projeto is not None:
        formulario.fields["projeto"].disabled = True

    if request.method == "POST" and formulario.is_valid() and salvar_formulario(formulario):
        if projeto is not None:
            messages.success(request, "Tarefa criada com sucesso.")
            return redirect("projeto_detalhe", pk=projeto.pk)
        messages.success(request, "Registro salvo com sucesso.")
        return redirect("gestao_lista", secao=secao)

    return render(request, "gestao_form.html", {
        "secao": secao,
        "secao_titulo": titulo,
        "titulo": ACOES_NOVO[secao],
        "projeto_contexto": projeto,
        "formulario": formulario,
    })


@login_required
def gestao_editar(request, secao, pk):
    modelo, titulo = configuracao(secao)

    registro = get_object_or_404(scoped_queryset(request, modelo), pk=pk)
    registrar_contato = (
        secao == "leads" and request.method == "POST"
        and request.POST.get("acao") == "registrar_contato"
    )
    form_contato = None
    somente_leitura = not pode_editar(request)
    if secao == "leads" and not somente_leitura:
        retorno = registro.proximo_retorno
        form_contato = RegistrarContatoForm(
            request.POST if registrar_contato else None,
            initial={"proximo_retorno": retorno if retorno and retorno >= timezone.localdate() else None},
            prefix="contato",
        )
        if registrar_contato and form_contato.is_valid():
            registro.ultimo_contato = timezone.localdate()
            registro.proximo_retorno = form_contato.cleaned_data["proximo_retorno"]
            registro.save(update_fields=["ultimo_contato", "proximo_retorno", "atualizado_em"])
            messages.success(request, "Contato registrado.")
            return redirect("gestao_editar", secao=secao, pk=registro.pk)

    Formulario = criar_formulario(modelo)
    dados = request.POST if request.method == "POST" and not registrar_contato else None
    formulario = scope_form(request, Formulario(dados, instance=registro))
    if somente_leitura:
        for campo in formulario.fields.values():
            campo.disabled = True

    if formulario.is_bound and formulario.is_valid() and salvar_formulario(formulario):
        messages.success(request, "Registro salvo com sucesso.")
        return redirect("gestao_lista", secao=secao)

    return render(request, "gestao_form.html", {
        "secao": secao,
        "secao_titulo": titulo,
        "titulo": str(registro),
        "status": getattr(registro, "get_status_display", lambda: "")(),
        "status_class": STATUS_CLASSES.get(getattr(registro, "status", "")),
        "projeto_detalhe_id": registro.pk if secao == "projetos" else None,
        "formulario": formulario,
        "form_contato": form_contato,
        "registro_id": registro.pk,
        "ultimo_contato": registro.ultimo_contato if secao == "leads" else None,
        "somente_leitura": somente_leitura,
    })
