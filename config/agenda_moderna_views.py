import calendar
from collections import defaultdict
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import formats, timezone

from apps.accounts.access import scoped_queryset
from apps.clients.models import Cliente
from apps.tasks.models import Tarefa


@login_required
def agenda(request):
    hoje = timezone.localdate()
    modo = request.GET.get("visao")
    mensal = modo == "mensal" or (modo != "semanal" and "mes" in request.GET)
    parametro = request.GET.get("mes" if mensal else "semana", "")
    try:
        referencia = date.fromisoformat(parametro + "-01" if mensal else parametro)
        if not 2000 <= referencia.year <= 2100:
            raise ValueError
    except ValueError:
        referencia = hoje

    if mensal:
        inicio = referencia.replace(day=1)
        anterior = (inicio - timedelta(days=1)).replace(day=1)
        fim = inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1])
        proximo = fim + timedelta(days=1)
        calendario = calendar.Calendar().monthdatescalendar(inicio.year, inicio.month)
        periodo = formats.date_format(inicio, "F Y")
    else:
        inicio = referencia - timedelta(days=referencia.weekday())
        fim = inicio + timedelta(days=6)
        anterior = inicio - timedelta(days=7)
        proximo = inicio + timedelta(days=7)
        calendario = [[inicio + timedelta(days=i) for i in range(7)]]
        periodo = f"{inicio:%d/%m} a {fim:%d/%m/%Y}"

    cliente_id = request.GET.get("cliente", "")
    if not (cliente_id.isascii() and cliente_id.isdecimal() and len(cliente_id) <= 19):
        cliente_id = ""
    minhas = request.GET.get("minhas") == "1"
    tarefas = scoped_queryset(request, Tarefa).select_related("projeto").order_by("prazo", "pk")
    if cliente_id:
        tarefas = tarefas.filter(projeto__cliente_id=cliente_id)
    if minhas:
        tarefas = tarefas.filter(responsavel=request.user)

    por_dia = defaultdict(list)
    for tarefa in tarefas.filter(prazo__range=(inicio, fim)):
        por_dia[tarefa.prazo].append(tarefa)

    semanas = [
        [
            {"data": dia, "dentro": inicio <= dia <= fim, "hoje": dia == hoje,
             "tarefas": por_dia.get(dia, ())}
            for dia in semana
        ]
        for semana in calendario
    ]
    atrasadas = (
        tarefas.filter(prazo__lt=hoje).exclude(prazo__range=(inicio, fim)).exclude(status=Tarefa.Status.CONCLUIDA)
        if not mensal else ()
    )
    ancora = hoje if inicio <= hoje <= fim else inicio
    formato = "%Y-%m" if mensal else "%Y-%m-%d"

    return render(request, "agenda.html", {
        "mensal": mensal,
        "semanas": semanas,
        "periodo": periodo,
        "periodo_atual": inicio.strftime(formato),
        "anterior": anterior.strftime(formato),
        "proximo": proximo.strftime(formato),
        "semana_referencia": ancora.isoformat(),
        "mes_referencia": ancora.strftime("%Y-%m"),
        "cliente_id": cliente_id,
        "clientes": scoped_queryset(request, Cliente).order_by("nome"),
        "minhas": minhas,
        "atrasadas": atrasadas,
        "total_tarefas": sum(len(v) for v in por_dia.values()),
    })
