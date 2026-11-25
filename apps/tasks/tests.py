from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Workspace, WorkspaceMembership
from apps.clients.models import Cliente
from apps.finance.models import Cobranca, Contrato, Pagamento
from apps.projects.models import Projeto
from apps.tasks.models import Tarefa


class CorrecoesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("equipe", password="senha-de-teste-123")
        cls.workspace = Workspace.objects.create(nome="Empresa", slug="empresa")
        WorkspaceMembership.objects.create(workspace=cls.workspace, usuario=cls.user)
        cliente = Cliente.objects.create(workspace=cls.workspace, nome="Cliente")
        cls.projeto = Projeto.objects.create(cliente=cliente, nome="Projeto")
        cls.tarefa = Tarefa.objects.create(projeto=cls.projeto, titulo="Tarefa")
        contrato = Contrato.objects.create(
            cliente=cliente, descricao="Site", valor=Decimal("100"), inicio=date(2026, 1, 1),
        )
        cls.cobranca = Cobranca.objects.create(
            contrato=contrato, competencia=date(2026, 1, 1),
            vencimento=date(2026, 1, 10), valor=Decimal("100"),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_kanban_recusa_tarefa_id_invalido_sem_erro_500(self):
        for valor in ("abc", "", "1.5", "9" * 25):
            resposta = self.client.post(reverse("kanban"), {"tarefa_id": valor, "status": "andamento"})
            self.assertEqual(resposta.status_code, 400, valor)
        self.tarefa.refresh_from_db()
        self.assertEqual(self.tarefa.status, Tarefa.Status.FAZER)

    def test_kanban_continua_movendo_tarefa(self):
        resposta = self.client.post(
            reverse("kanban"), {"tarefa_id": str(self.tarefa.pk), "status": "andamento"},
        )
        self.assertEqual(resposta.status_code, 302)
        self.tarefa.refresh_from_db()
        self.assertEqual(self.tarefa.status, Tarefa.Status.ANDAMENTO)

    def test_pagamento_nao_ultrapassa_cobranca_ao_gravar(self):
        Pagamento.objects.create(cobranca=self.cobranca, data=date(2026, 1, 5), valor=Decimal("70"))
        with self.assertRaises(ValidationError):
            Pagamento.objects.create(cobranca=self.cobranca, data=date(2026, 1, 6), valor=Decimal("40"))
        Pagamento.objects.create(cobranca=self.cobranca, data=date(2026, 1, 6), valor=Decimal("30"))
        self.assertEqual(self.cobranca.saldo, Decimal("0"))

    def test_pagamento_em_cobranca_cancelada_bloqueado(self):
        self.cobranca.situacao = Cobranca.Situacao.CANCELADA
        self.cobranca.save()
        with self.assertRaises(ValidationError):
            Pagamento.objects.create(cobranca=self.cobranca, data=date(2026, 1, 5), valor=Decimal("10"))

    def test_rotulos_com_acentos_corretos(self):
        self.assertEqual(Tarefa.Status.CONCLUIDA.label, "Concluída")
        self.assertEqual(Projeto.Status.CONCLUIDO.label, "Concluído")
