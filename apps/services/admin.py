from django.contrib import admin

from apps.accounts.admin import WorkspaceModelAdmin
from .models import Servico, ModeloTarefa

class ModeloTarefaInline(admin.TabularInline):
    model = ModeloTarefa
    extra = 1

@admin.register(Servico)
class ServicoAdmin(WorkspaceModelAdmin):
    list_display = ("nome", "ativo")
    search_fields = ("nome",)
    inlines = [ModeloTarefaInline]
