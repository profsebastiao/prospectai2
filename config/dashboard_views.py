from calendar import monthcalendar
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.shortcuts import render
from django.utils import formats, timezone

from apps.accounts.access import scoped_queryset
from apps.clients.models import Cliente
from apps.leads.models import LEAD_STAGES_CLOSED, Lead
from apps.projects.models import Projeto
from apps.tasks.models import Tarefa


def prazos_por_semana(pendentes, hoje):
    segunda = hoje - timedelta(days=hoje.weekday())
    fim = segunda + timedelta(weeks=6)
    prazos = pendentes.filter(prazo__gte=hoje, prazo__lt=fim).values_list("prazo", flat=True)
    totais = [0] * 6
    for prazo in prazos:
        totais[(prazo - segunda).days // 7] += 1
    maior = max(totais)
    if not maior:
        return []
    return [
        {"label": (segunda + timedelta(weeks=i)).strftime("%d/%m"),
         "total": total, "altura": round(100 * total / maior)}
        for i, total in enumerate(totais)
    ]


def projetos_por_status(projetos):
    contagem = {
        linha["status"]: linha["total"]
        for linha in projetos.values("status").annotate(total=Count("pk")).order_by()
    }
    cores = ("#7773db", "#277965", "#b97121", "#4446a0")
    total = sum(contagem.values())
    partes, segmentos = [], []
    inicio = acumulado = 0
    for (codigo, nome), cor in zip(Projeto.Status.choices, cores):
        quantidade = contagem.get(codigo, 0)
        if not quantidade:
            continue
        acumulado += quantidade
        fim = acumulado / total * 100
        segmentos.append(f"{cor} {inicio:.3f}% {fim:.3f}%")
        partes.append({"codigo": codigo, "nome": nome, "total": quantidade, "cor": cor})
        inicio = fim
    return total, partes, ", ".join(segmentos)


def evolucao_comercial(leads, clientes, hoje):
    indice = hoje.year * 12 + hoje.month - 1
    meses = [
        hoje.replace(year=mes // 12, month=mes % 12 + 1, day=1)
        for mes in range(indice - 5, indice + 1)
    ]

    def contagem_mensal(registros):
        linhas = (
            registros.filter(criado_em__date__range=(meses[0], hoje))
            .annotate(mes=TruncMonth("criado_em"))
            .values("mes").annotate(total=Count("pk")).order_by()
        )
        return {linha["mes"].date(): linha["total"] for linha in linhas}

    leads_por_mes = contagem_mensal(leads)
    clientes_por_mes = contagem_mensal(clientes)
    maior = max([0, *leads_por_mes.values(), *clientes_por_mes.values()])
    if not maior:
        return []
    return [
        {
            "label": formats.date_format(mes, "b").capitalize(),
            "periodo": formats.date_format(mes, "F Y"),
            "leads": leads_por_mes.get(mes, 0),
            "clientes": clientes_por_mes.get(mes, 0),
            "leads_y": 130 - round(112 * leads_por_mes.get(mes, 0) / maior),
            "clientes_y": 130 - round(112 * clientes_por_mes.get(mes, 0) / maior),
            "x": round((i + 0.5) * 400 / len(meses)),
        }
        for i, mes in enumerate(meses)
    ]


@login_required
def dashboard(request):
    hoje = timezone.localdate()
    leads = scoped_queryset(request, Lead)
    clientes = scoped_queryset(request, Cliente)
    projetos = scoped_queryset(request, Projeto)
    tarefas = scoped_queryset(request, Tarefa)
    pendentes = tarefas.exclude(status=Tarefa.Status.CONCLUIDA)
    total_projetos, status_projetos, rosca = projetos_por_status(projetos)
    retornos = leads.exclude(status__in=LEAD_STAGES_CLOSED).aggregate(
        retornos_hoje=Count("pk", filter=Q(proximo_retorno=hoje)),
        retornos_atrasados=Count("pk", filter=Q(proximo_retorno__lt=hoje)),
    )
    context = {
        **retornos,
        "minhas_tarefas_hoje": pendentes.filter(prazo=hoje, responsavel=request.user).count(),
        "total_leads": leads.count(),
        "total_clientes": clientes.count(),
        "projetos_ativos": total_projetos - sum(
            parte["total"] for parte in status_projetos
            if parte["codigo"] == Projeto.Status.CONCLUIDO
        ),
        "tarefas_pendentes": pendentes.count(),
        "atividades": tarefas.select_related("projeto").order_by("-criado_em", "-pk")[:5],
        "hoje": hoje,
        "prazos_semanais": prazos_por_semana(pendentes, hoje),
        "total_projetos": total_projetos,
        "status_projetos": status_projetos,
        "rosca": rosca,
        "evolucao": evolucao_comercial(leads, clientes, hoje),
    }
    return render(request, "dashboard.html", context)


@login_required
def agenda(request):
    from apps.clients.models import Cliente
    from apps.tasks.models import Tarefa

    hoje = timezone.localdate()
    parametro = request.GET.get("mes", "")

    try:
        primeiro = date.fromisoformat(parametro + "-01")
    except (ValueError, TypeError):
        primeiro = hoje.replace(day=1)

    cliente_id = request.GET.get("cliente", "")
    tarefas = scoped_queryset(request, Tarefa).filter(
        prazo__year=primeiro.year,
        prazo__month=primeiro.month
    ).select_related("projeto", "projeto__cliente")

    if cliente_id.isdigit():
        tarefas = tarefas.filter(projeto__cliente_id=int(cliente_id))
    else:
        cliente_id = ""

    por_dia = {}
    for tarefa in tarefas.order_by("prazo", "id"):
        por_dia.setdefault(tarefa.prazo.day, []).append(tarefa)

    semanas = []
    for semana in monthcalendar(primeiro.year, primeiro.month):
        dias = []
        for numero in semana:
            dias.append({
                "numero": numero,
                "tarefas": por_dia.get(numero, []) if numero else [],
                "hoje": numero == hoje.day
                        and primeiro.month == hoje.month
                        and primeiro.year == hoje.year,
            })
        semanas.append(dias)

    anterior = primeiro - timedelta(days=1)
    if primeiro.month == 12:
        proximo = date(primeiro.year + 1, 1, 1)
    else:
        proximo = date(primeiro.year, primeiro.month + 1, 1)

    return render(request, "agenda.html", {
        "semanas": semanas,
        "mes_atual": primeiro.strftime("%Y-%m"),
        "mes_titulo": primeiro.strftime("%m/%Y"),
        "mes_anterior": anterior.strftime("%Y-%m"),
        "mes_proximo": proximo.strftime("%Y-%m"),
        "clientes": scoped_queryset(request, Cliente).order_by("nome"),
        "cliente_id": cliente_id,
        "total_tarefas": tarefas.count(),
    })
