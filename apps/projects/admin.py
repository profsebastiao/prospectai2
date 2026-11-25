from django.contrib import admin

from apps.accounts.admin import WorkspaceModelAdmin, WorkspaceForeignKeys
from .models import Projeto, ProjetoServico

class ProjetoServicoInline(WorkspaceForeignKeys, admin.TabularInline):
    model = ProjetoServico
    extra = 1

@admin.register(Projeto)
class ProjetoAdmin(WorkspaceModelAdmin):
    list_display = (
        "nome", "cliente", "status", "prazo_entrega"
    )
    list_filter = ("status",)
    search_fields = ("nome", "cliente__nome")
    inlines = [ProjetoServicoInline]
