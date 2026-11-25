from django.contrib import admin

from apps.accounts.admin import WorkspaceModelAdmin
from .models import Lead


@admin.register(Lead)
class LeadAdmin(WorkspaceModelAdmin):
    list_display = (
        "nome", "empresa", "whatsapp",
        "status", "criado_em"
    )
    search_fields = ("nome", "empresa", "email", "whatsapp")
    list_filter = ("status", "criado_em")
