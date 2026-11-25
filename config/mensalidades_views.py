from io import StringIO
import re

from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.accounts.access import scoped_queryset
from apps.finance.models import Contrato


@login_required
@require_http_methods(["GET", "POST"])
def gerar_mensalidades_pagina(request):
    mes = timezone.localdate().strftime("%Y-%m")
    meses = "1"
    contrato_id = ""
    confirmacao = False
    resultado = ""
    erro = ""
    executado = False

    if request.method == "POST":
        mes = request.POST.get("mes", "").strip()
        meses = request.POST.get("meses", "1").strip()
        contrato_id = request.POST.get("contrato", "").strip()
        acao = request.POST.get("acao", "")
        confirmacao = (
            request.POST.get("confirmar") == "sim"
        )

        if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", mes):
            erro = "Mes invalido. Utilize AAAA-MM."
        elif not meses.isdigit() or not 1 <= int(meses) <= 12:
            erro = "Selecione um periodo de 1 a 12 meses."
        elif contrato_id and (
            not contrato_id.isdigit()
            or int(contrato_id) <= 0
        ):
            erro = "Contrato invalido."
        elif acao not in ("simular", "gerar"):
            erro = "Acao invalida."
        elif acao == "gerar" and not confirmacao:
            erro = "Confirme a geracao das cobrancas."
        else:
            opcoes = {
                "mes": mes,
                "workspace": request.workspace.pk,
                "meses": int(meses),
            }

            if contrato_id:
                opcoes["contrato"] = int(contrato_id)

            if acao == "gerar":
                opcoes["aplicar"] = True

            saida = StringIO()

            try:
                if acao == "gerar":
                    # Operacao de gravacao inteiramente
                    # protegida por uma transacao.
                    with transaction.atomic():
                        call_command(
                            "gerar_mensalidades",
                            stdout=saida,
                            **opcoes
                        )
                else:
                    call_command(
                        "gerar_mensalidades",
                        stdout=saida,
                        **opcoes
                    )

                resultado = saida.getvalue()
                executado = acao == "gerar"

            except CommandError as exc:
                erro = str(exc)
            except Exception:
                erro = (
                    "Nao foi possivel processar. "
                    "Consulte os logs do servidor."
                )
                raise

    return render(
        request,
        "gerar_mensalidades.html",
        {
            "mes": mes,
            "meses": meses,
            "contrato_id": contrato_id,
            "contratos": scoped_queryset(request, Contrato).filter(
                ativo=True, periodicidade="mensal"
            ).order_by("pk"),
            "confirmacao": confirmacao,
            "resultado": resultado,
            "erro": erro,
            "executado": executado,
            "pode_gerar": True,
        },
    )
