from django.contrib import admin

from apps.accounts.admin import WorkspaceModelAdmin
from .models import Cliente


@admin.register(Cliente)
class ClienteAdmin(WorkspaceModelAdmin):
    list_display = ("nome", "empresa", "whatsapp", "ativo")
    search_fields = ("nome", "empresa", "email", "whatsapp")
    list_filter = ("ativo",)
