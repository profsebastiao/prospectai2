from django.contrib import admin

from apps.accounts.admin import WorkspaceModelAdmin
from .models import Contrato, Cobranca, Pagamento


@admin.register(Contrato)
class ContratoAdmin(WorkspaceModelAdmin):
    list_display = (
        "descricao", "cliente", "valor",
        "periodicidade", "inicio", "ativo"
    )
    list_filter = ("periodicidade", "ativo")
    search_fields = ("descricao", "cliente__nome")


@admin.register(Cobranca)
class CobrancaAdmin(WorkspaceModelAdmin):
    list_display = (
        "contrato", "competencia",
        "vencimento", "valor", "situacao"
    )
    list_filter = ("situacao", "vencimento")
    search_fields = (
        "contrato__descricao", "contrato__cliente__nome"
    )


@admin.register(Pagamento)
class PagamentoAdmin(WorkspaceModelAdmin):
    list_display = ("cobranca", "data", "valor", "forma")
    list_filter = ("forma", "data")
    search_fields = ("referencia", "cobranca__contrato__descricao")
