from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.clients.models import Cliente
from apps.finance.models import Contrato
from apps.projects.models import Projeto
from apps.tasks.models import Tarefa

from .models import Workspace, WorkspaceMembership

Papel = WorkspaceMembership.Papel


class PapeisTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.workspace = Workspace.objects.create(nome="Empresa", slug="empresa")
        cls.usuarios = {}
        for papel in Papel.values:
            user = get_user_model().objects.create_user(papel, password="senha-de-teste-123")
            WorkspaceMembership.objects.create(workspace=cls.workspace, usuario=user, papel=papel)
            cls.usuarios[papel] = user
        cls.cliente = Cliente.objects.create(workspace=cls.workspace, nome="Cliente")
        cls.projeto = Projeto.objects.create(cliente=cls.cliente, nome="Projeto")
        cls.tarefa = Tarefa.objects.create(projeto=cls.projeto, titulo="Tarefa")
        cls.contrato = Contrato.objects.create(
            cliente=cls.cliente, descricao="Site", valor=Decimal("100"), inicio=date(2026, 1, 1),
        )

    def entrar(self, papel):
        self.client.force_login(self.usuarios[papel])

    def test_novo_vinculo_comeca_como_equipe(self):
        user = get_user_model().objects.create_user("novo")
        vinculo = WorkspaceMembership.objects.create(workspace=self.workspace, usuario=user)
        self.assertEqual(vinculo.papel, Papel.EQUIPE)

    def test_financeiro_so_para_gestor(self):
        urls = [
            reverse("financeiro"),
            reverse("gerar_mensalidades_pagina"),
            reverse("gestao_lista", args=["contratos"]),
            reverse("gestao_novo", args=["cobrancas"]),
            reverse("gestao_editar", args=["contratos", self.contrato.pk]),
        ]
        for papel, esperado in ((Papel.GESTOR, 200), (Papel.EQUIPE, 403), (Papel.LEITURA, 403)):
            self.entrar(papel)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, esperado, (papel, url))

    def test_menu_esconde_financeiro(self):
        self.entrar(Papel.EQUIPE)
        self.assertNotContains(self.client.get(reverse("dashboard")), reverse("financeiro"))
        self.entrar(Papel.GESTOR)
        self.assertContains(self.client.get(reverse("dashboard")), reverse("financeiro"))

    def test_equipe_edita_crm_e_tarefas(self):
        self.entrar(Papel.EQUIPE)
        resposta = self.client.post(reverse("kanban"), {"tarefa_id": self.tarefa.pk, "status": "andamento"})
        self.assertEqual(resposta.status_code, 302)
        resposta = self.client.post(reverse("portal_cliente_novo"), {"nome": "Novo cliente", "ativo": "on"})
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(Cliente.objects.filter(nome="Novo cliente", workspace=self.workspace).exists())

    def test_leitura_ve_mas_nao_altera(self):
        self.entrar(Papel.LEITURA)
        for url in (
            reverse("dashboard"), reverse("kanban"), reverse("agenda"), reverse("portal_clientes"),
            reverse("gestao_lista", args=["tarefas"]), reverse("projeto_detalhe", args=[self.projeto.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)

        resposta = self.client.get(reverse("gestao_editar", args=["tarefas", self.tarefa.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.context["somente_leitura"])
        self.assertNotContains(resposta, 'type="submit">Salvar<')

        self.assertEqual(self.client.post(
            reverse("kanban"), {"tarefa_id": self.tarefa.pk, "status": "andamento"},
        ).status_code, 403)
        self.assertEqual(self.client.post(
            reverse("gestao_editar", args=["tarefas", self.tarefa.pk]), {"titulo": "Alterada"},
        ).status_code, 403)
        for rota in ("portal_cliente_novo", "projeto_contratacao_novo"):
            self.assertEqual(self.client.get(reverse(rota)).status_code, 403, rota)
        self.assertEqual(self.client.get(reverse("gestao_novo", args=["tarefas"])).status_code, 403)
        self.tarefa.refresh_from_db()
        self.assertEqual(self.tarefa.status, Tarefa.Status.FAZER)
        self.assertEqual(self.tarefa.titulo, "Tarefa")

    def test_leitura_ainda_altera_o_proprio_perfil(self):
        self.entrar(Papel.LEITURA)
        resposta = self.client.post(reverse("configuracoes"), {
            "acao": "perfil", "first_name": "Ana", "last_name": "", "email": "",
        })
        self.assertEqual(resposta.status_code, 302)
        self.usuarios[Papel.LEITURA].refresh_from_db()
        self.assertEqual(self.usuarios[Papel.LEITURA].first_name, "Ana")

    def test_superusuario_conta_como_gestor(self):
        admin = get_user_model().objects.create_superuser("plataforma", password="senha-de-teste-123")
        self.client.force_login(admin)
        sessao = self.client.session
        sessao["workspace_id"] = self.workspace.pk
        sessao.save()
        self.assertEqual(self.client.get(reverse("financeiro")).status_code, 200)
