"""Rotas do ProspectAI."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic.base import RedirectView

from apps.accounts.views import workspace_select
from config import prospeccao_views
from config.agenda_moderna_views import agenda
from config.busca_leads_views import buscar_leads_web
from config.contratacao_views import projeto_contratacao_novo
from config.conversao_views import conversao_leads
from config.dashboard_views import dashboard
from config.detalhes_views import (
    cliente_detalhe, configuracoes, foto_perfil_usuario, projeto_detalhe,
)
from config.financeiro_views import financeiro
from config.gestao_views import gestao_editar, gestao_lista, gestao_novo
from config.groq_integracao_views import integracao_groq
from config.kanban_views import kanban
from config.mensalidades_views import gerar_mensalidades_pagina
from config.portal_views import (
    portal_administracao, portal_cliente_editar, portal_cliente_novo, portal_clientes,
)


urlpatterns = [
    # Detalhes e conta
    path("clientes/<int:pk>/", cliente_detalhe, name="cliente_detalhe"),
    path("projetos/<int:pk>/", projeto_detalhe, name="projeto_detalhe"),
    path("configuracoes/", configuracoes, name="configuracoes"),
    path("conta/foto/", foto_perfil_usuario, name="foto_perfil_usuario"),

    # Gestão genérica (leads, serviços, projetos, tarefas, financeiro)
    path("gestao/<slug:secao>/", gestao_lista, name="gestao_lista"),
    path("gestao/<slug:secao>/novo/", gestao_novo, name="gestao_novo"),
    path("gestao/<slug:secao>/<int:pk>/editar/", gestao_editar, name="gestao_editar"),

    # Painéis
    path("kanban/", kanban, name="kanban"),
    path("", dashboard, name="dashboard"),
    path("agenda/", agenda, name="agenda"),
    path("admin/", admin.site.urls),

    # Portal de clientes
    path("clientes/", portal_clientes, name="portal_clientes"),
    path("clientes/novo/", portal_cliente_novo, name="portal_cliente_novo"),
    path("clientes/<int:pk>/editar/", portal_cliente_editar, name="portal_cliente_editar"),
    path("administracao/", portal_administracao, name="portal_administracao"),

    # Comercial: busca, prospecção com IA e conversão de leads
    path("comercial/leads/buscar/", buscar_leads_web, name="buscar_leads_web"),
    path("comercial/prospeccao/historico/", prospeccao_views.historico, name="prospeccao_historico"),
    path("comercial/prospeccao/pesquisas/<int:pk>/", prospeccao_views.pesquisa, name="prospeccao_pesquisa"),
    path("comercial/prospeccao/analises/<int:pk>/", prospeccao_views.analise, name="prospeccao_analise"),
    path("comercial/prospeccao/relatorios/<int:pk>/", prospeccao_views.relatorio, name="prospeccao_relatorio"),
    path("comercial/prospeccao/relatorios/<int:pk>/baixar/", prospeccao_views.exportar, name="prospeccao_exportar"),
    path("comercial/prospeccao/<slug:modo>/", prospeccao_views.iniciar, name="prospeccao_iniciar"),
    path("comercial/conversao/", conversao_leads, name="conversao_leads"),

    # Operação e financeiro
    path("operacao/projetos/novo/", projeto_contratacao_novo, name="projeto_contratacao_novo"),
    path("financeiro/", financeiro, name="financeiro"),
    path("financeiro/mensalidades/", gerar_mensalidades_pagina, name="gerar_mensalidades_pagina"),

    # Empresa ativa, integrações e autenticação
    path("espacos/", workspace_select, name="workspace_select"),
    path("configuracoes/integracoes/groq/", integracao_groq, name="integracao_groq"),
    path(
        "configuracoes/integracoes/gemini/",
        RedirectView.as_view(pattern_name="integracao_groq", permanent=False),
        name="integracao_gemini",
    ),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="login.html", redirect_authenticated_user=True),
        name="prospectai_login",
    ),
    path("sair/", auth_views.LogoutView.as_view(), name="prospectai_logout"),
]
