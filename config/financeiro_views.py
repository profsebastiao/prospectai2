from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.access import scoped_queryset
from apps.clients.models import Cliente
from apps.finance.models import Contrato, Cobranca, Pagamento


def dinheiro(valor):
    valor = valor if valor is not None else Decimal("0.00")
    texto = f"{valor:,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


@login_required
def financeiro(request):
    hoje = timezone.localdate()

    try:
        ano, mes = map(int, request.GET.get("mes", "").split("-"))
        inicio = date(ano, mes, 1)
    except (ValueError, TypeError):
        inicio = hoje.replace(day=1)

    fim = date(
        inicio.year,
        inicio.month,
        monthrange(inicio.year, inicio.month)[1]
    )

    cliente_id = request.GET.get("cliente", "")
    if not cliente_id.isdigit():
        cliente_id = ""

    contratos = scoped_queryset(request, Contrato).all()
    cobrancas = scoped_queryset(request, Cobranca).select_related(
        "contrato", "contrato__cliente"
    ).prefetch_related("pagamentos").filter(
        vencimento__range=(inicio, fim)
    )
    pagamentos = scoped_queryset(request, Pagamento).filter(
        data__range=(inicio, fim)
    )

    if cliente_id:
        contratos = contratos.filter(cliente_id=int(cliente_id))
        cobrancas = cobrancas.filter(
            contrato__cliente_id=int(cliente_id)
        )
        pagamentos = pagamentos.filter(
            cobranca__contrato__cliente_id=int(cliente_id)
        )

    recebido = pagamentos.aggregate(
        total=Sum("valor")
    )["total"] or Decimal("0.00")

    aberto = Decimal("0.00")
    vencido = Decimal("0.00")
    linhas = []

    for cobranca in cobrancas.order_by("vencimento", "pk"):
        pago = sum(
            (p.valor for p in cobranca.pagamentos.all()),
            Decimal("0.00")
        )
        saldo = max(cobranca.valor - pago, Decimal("0.00"))

        if cobranca.situacao == "cancelada":
            situacao = "Cancelada"
        elif saldo == 0:
            situacao = "Quitada"
        elif cobranca.vencimento < hoje:
            situacao = "Vencida"
            vencido += saldo
            aberto += saldo
        else:
            situacao = "Em aberto"
            aberto += saldo

        linhas.append({
            "cobranca": cobranca,
            "cliente": cobranca.contrato.cliente.nome,
            "valor": dinheiro(cobranca.valor),
            "pago": dinheiro(pago),
            "saldo": dinheiro(saldo),
            "situacao": situacao,
        })

    return render(request, "financeiro.html", {
        "mes": inicio.strftime("%Y-%m"),
        "clientes": scoped_queryset(request, Cliente).order_by("nome"),
        "cliente_id": cliente_id,
        "recebido": dinheiro(recebido),
        "aberto": dinheiro(aberto),
        "vencido": dinheiro(vencido),
        "contratos_ativos": contratos.filter(ativo=True).count(),
        "linhas": linhas,
        "pode_contrato": True,
        "pode_cobranca": True,
        "pode_pagamento": True,
    })
