from django.contrib import admin

from apps.accounts.admin import WorkspaceModelAdmin
from .models import Tarefa

@admin.register(Tarefa)
class TarefaAdmin(WorkspaceModelAdmin):
    list_display = (
        "titulo", "projeto", "status",
        "prioridade", "responsavel", "prazo"
    )
    list_filter = ("status", "prioridade", "prazo")
    search_fields = ("titulo", "projeto__nome")
    list_editable = ("status", "prioridade")
