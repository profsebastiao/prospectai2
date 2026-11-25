from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.forms import modelform_factory
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.access import scope_form, scoped_queryset
from apps.clients.models import Cliente


ClienteForm = modelform_factory(
    Cliente,
    fields=[
        "nome", "empresa", "email", "telefone",
        "whatsapp", "ativo", "observacoes"
    ],
)


@login_required
def portal_clientes(request):
    busca = request.GET.get("q", "").strip()[:100]
    registros = scoped_queryset(request, Cliente).order_by("nome", "pk")

    if busca:
        registros = registros.filter(
            Q(nome__icontains=busca)
            | Q(empresa__icontains=busca)
            | Q(email__icontains=busca)
        )

    pagina = Paginator(registros, 15).get_page(
        request.GET.get("page")
    )

    return render(request, "portal_clientes.html", {
        "pagina": pagina,
        "busca": busca,
        "pode_criar": True,
        "pode_editar": True,
    })


@login_required
def portal_cliente_novo(request):
    form = scope_form(request, ClienteForm(request.POST or None))

    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("portal_clientes")

    return render(request, "portal_cliente_form.html", {
        "form": form,
        "titulo": "Novo cliente",
    })


@login_required
def portal_cliente_editar(request, pk):
    cliente = get_object_or_404(scoped_queryset(request, Cliente), pk=pk)
    form = ClienteForm(request.POST or None, instance=cliente)

    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("portal_clientes")

    return render(request, "portal_cliente_form.html", {
        "form": form,
        "titulo": "Editar cliente",
    })


@login_required
def portal_administracao(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    return render(request, "portal_administracao.html")
