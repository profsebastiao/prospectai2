from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import F, Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.access import pode_editar, scoped_queryset
from apps.clients.models import Cliente
from apps.projects.models import Projeto
from apps.tasks.models import Tarefa


@login_required
def kanban(request):
    cliente_id = request.GET.get("cliente", "")
    projeto_id = request.GET.get("projeto", "")
    busca = request.GET.get("q", "").strip()[:100]
    minhas = request.GET.get("minhas") == "1"

    clientes = scoped_queryset(request, Cliente).order_by("nome")
    projetos = scoped_queryset(request, Projeto).select_related("cliente").order_by("nome")

    if cliente_id.isdigit():
        projetos = projetos.filter(cliente_id=int(cliente_id))
    else:
        cliente_id = ""

    if not projeto_id.isdigit():
        projeto_id = ""

    if projeto_id and not projetos.filter(pk=int(projeto_id)).exists():
        projeto_id = ""

    tarefas = scoped_queryset(request, Tarefa).select_related(
        "projeto", "projeto__cliente"
    )

    if cliente_id:
        tarefas = tarefas.filter(projeto__cliente_id=int(cliente_id))
    if projeto_id:
        tarefas = tarefas.filter(projeto_id=int(projeto_id))

    if minhas:
        tarefas = tarefas.filter(responsavel=request.user)

    status_choices = Tarefa.Status.choices
    status_validos = {codigo for codigo, nome in status_choices}

    if request.method == "POST":
        tarefa_id = request.POST.get("tarefa_id")
        status = request.POST.get("status")

        if status not in status_validos:
            return HttpResponseForbidden("Status invalido.")
        if not tarefa_id or not tarefa_id.isascii() or not tarefa_id.isdecimal() or len(tarefa_id) > 19:
            return HttpResponseBadRequest("Tarefa invalida.")

        tarefa = get_object_or_404(tarefas, pk=tarefa_id)
        tarefa.status = status
        tarefa.save(update_fields=["status"])

        filtros = {}
        if cliente_id:
            filtros["cliente"] = cliente_id
        if projeto_id:
            filtros["projeto"] = projeto_id
        if busca:
            filtros["q"] = busca

        if minhas:
            filtros["minhas"] = "1"

        destino = reverse("kanban")
        if filtros:
            destino += "?" + urlencode(filtros)
        return redirect(destino)

    if busca:
        tarefas = tarefas.filter(
            Q(titulo__icontains=busca) | Q(projeto__nome__icontains=busca)
        )

    lista = list(tarefas.order_by(F("prazo").asc(nulls_last=True), "id"))

    colunas = [
        {
            "codigo": codigo,
            "nome": nome,
            "tarefas": [
                tarefa for tarefa in lista
                if tarefa.status == codigo
            ],
        }
        for codigo, nome in status_choices
    ]

    concluidas = sum(t.status == "concluida" for t in lista)
    hoje = timezone.localdate()

    atrasadas = sum(
        t.prazo is not None
        and t.prazo < hoje
        and t.status != "concluida"
        for t in lista
    )

    total = len(lista)

    return render(request, "kanban.html", {
        "clientes": clientes,
        "projetos": projetos,
        "cliente_id": cliente_id,
        "projeto_id": projeto_id,
        "busca": busca,
        "minhas": minhas,
        "colunas": colunas,
        "status_choices": status_choices,
        "pode_editar": pode_editar(request),
        "total": total,
        "pendentes": total - concluidas,
        "concluidas": concluidas,
        "atrasadas": atrasadas,
        "progresso": round(100 * concluidas / total) if total else 0,
        "hoje": hoje,
    })
