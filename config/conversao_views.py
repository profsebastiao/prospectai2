from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.access import scoped_queryset
from apps.leads.models import Lead
from apps.clients.models import Cliente


@login_required
@require_http_methods(["GET", "POST"])
def conversao_leads(request):

    if request.method == "POST":
        pk = request.POST.get("lead_id", "")
        if not pk.isdigit():
            return HttpResponseBadRequest("Lead invalido.")

        with transaction.atomic():
            lead = get_object_or_404(
                scoped_queryset(request, Lead).select_for_update(),
                pk=int(pk)
            )

            cliente, criado = scoped_queryset(request, Cliente).get_or_create(
                lead_origem=lead,
                defaults={
                    "workspace": request.workspace,
                    "nome": lead.nome,
                    "empresa": lead.empresa,
                    "email": lead.email,
                    "telefone": lead.telefone,
                    "whatsapp": lead.whatsapp,
                }
            )

            if lead.status != "convertido":
                lead.status = "convertido"
                lead.save(update_fields=["status"])

        if criado:
            messages.success(
                request,
                "Lead convertido em cliente com sucesso."
            )
        else:
            messages.info(
                request,
                "Este lead ja possui um cliente vinculado."
            )

        return redirect("conversao_leads")

    busca = request.GET.get("q", "").strip()[:100]
    leads = scoped_queryset(request, Lead).order_by("-pk")

    if busca:
        leads = leads.filter(
            Q(nome__icontains=busca)
            | Q(empresa__icontains=busca)
            | Q(email__icontains=busca)
        )

    pagina = Paginator(leads, 15).get_page(
        request.GET.get("page")
    )

    ids = [lead.pk for lead in pagina.object_list]

    vinculados = dict(
        scoped_queryset(request, Cliente).filter(lead_origem_id__in=ids)
        .values_list("lead_origem_id", "pk")
    )

    itens = [
        {"lead": lead, "cliente_id": vinculados.get(lead.pk)}
        for lead in pagina.object_list
    ]

    return render(request, "conversao_leads.html", {
        "itens": itens,
        "pagina": pagina,
        "busca": busca,
        "pode_converter": True,
        "pode_ver_clientes": True,
    })
