from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import Workspace, WorkspaceOwned


WORKSPACE_LOOKUPS = {
    "leads.lead": "workspace",
    "leads.pesquisaprospeccao": "workspace",
    "leads.analiseprospeccao": "pesquisa__workspace",
    "leads.relatorioprospeccao": "analise__pesquisa__workspace",
    "clients.cliente": "workspace",
    "services.servico": "workspace",
    "services.modelotarefa": "servico__workspace",
    "projects.projeto": "cliente__workspace",
    "projects.projetoservico": "projeto__cliente__workspace",
    "tasks.tarefa": "projeto__cliente__workspace",
    "finance.contrato": "cliente__workspace",
    "finance.cobranca": "contrato__cliente__workspace",
    "finance.pagamento": "cobranca__contrato__cliente__workspace",
}


def workspaces_for(user):
    if not user.is_authenticated or not user.is_active:
        return Workspace.objects.none()
    if user.is_superuser:
        return Workspace.objects.all()
    return Workspace.objects.filter(workspacemembership__usuario=user)


def scoped_queryset(request, model, queryset=None):
    if queryset is None:
        queryset = model._default_manager.all()
    if request.workspace is None:
        return queryset.none()
    if model is get_user_model():
        members = request.workspace.usuarios.values("pk")
        return queryset.filter(pk__in=members, is_active=True)
    lookup = WORKSPACE_LOOKUPS[model._meta.label_lower]
    return queryset.filter(**{lookup: request.workspace.pk})


def scope_form(request, form):
    if isinstance(form.instance, WorkspaceOwned):
        form.instance.workspace = request.workspace
    for field in form.fields.values():
        if getattr(field, "queryset", None) is not None:
            field.queryset = scoped_queryset(request, field.queryset.model, field.queryset)
    return form


class WorkspaceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.workspace = None
        if request.user.is_authenticated:
            available = workspaces_for(request.user)
            selected = request.session.get("workspace_id")
            if selected is not None:
                request.workspace = available.filter(pk=selected).first()
            if request.workspace is None:
                if request.user.is_superuser:
                    request.workspace = available.filter(slug="sdevbr").first() or available.first()
                else:
                    candidates = list(available[:2])
                    if len(candidates) == 1:
                        request.workspace = candidates[0]
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if match.namespace == "admin":
            if request.user.is_authenticated and not request.user.is_superuser:
                raise PermissionDenied
            return None
        if match.url_name in {
            "prospectai_login", "prospectai_logout", "workspace_select", "configuracoes", "foto_perfil_usuario"
        }:
            return None
        if request.user.is_authenticated and request.workspace is None:
            return redirect("workspace_select")
        return None