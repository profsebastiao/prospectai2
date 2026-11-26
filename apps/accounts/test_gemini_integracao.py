import base64
import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.gemini_credentials import criptografar, descriptografar
from apps.accounts.models import EventoIntegracaoGemini, IntegracaoGemini, Workspace, WorkspaceMembership


AES_TESTE = base64.urlsafe_b64encode(b"k" * 32).decode()
CHAVE_A = "AIza-fixture-empresa-a"
CHAVE_INVALIDA = "fixture-chave-recusada"


@override_settings(GEMINI_ENCRYPTION_KEYS=AES_TESTE, IA_COMPANY_DAILY_LIMIT=12, GEMINI_MODEL="gemini-3.8-flash")
class IntegracaoGeminiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user("gestor")
        cls.member = get_user_model().objects.create_user("membro")
        cls.a = Workspace.objects.create(nome="Marcenaria", slug="marcenaria-ia")
        cls.b = Workspace.objects.create(nome="Agencia", slug="agencia-ia")
        WorkspaceMembership.objects.create(usuario=cls.admin, workspace=cls.a, pode_gerenciar_gemini=True)
        WorkspaceMembership.objects.create(usuario=cls.admin, workspace=cls.b, pode_gerenciar_gemini=True)
        WorkspaceMembership.objects.create(usuario=cls.member, workspace=cls.a)

    def setUp(self):
        self.client.force_login(self.admin)
        self.selecionar(self.a)
        self.url = reverse("integracao_ia")

    def selecionar(self, workspace):
        sessao = self.client.session
        sessao["workspace_id"] = workspace.pk
        sessao.save()

    def pedido(self, acao, **dados):
        return self.client.post(self.url, {"acao": acao, **dados}, secure=True)

    @staticmethod
    def simular(opener, chave, *, rejeitar=False):
        def abrir(req, timeout):
            assert req.get_header("X-goog-api-key") == chave
            if rejeitar:
                raise HTTPError(req.full_url, 400, "bad request", {}, io.BytesIO(
                    b'{"error":{"message":"API key not valid.","status":"INVALID_ARGUMENT","details":[{"reason":"API_KEY_INVALID"}]}}'
                ))
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps({"candidates": [{
                "finishReason": "STOP", "content": {"parts": [{"text": "{\"ok\":true}"}]},
            }]}).encode()
            return response
        opener.return_value.open.side_effect = abrir

    @patch("apps.leads.gemini.build_opener")
    def test_salva_credencial_encriptada_por_empresa_e_status_sem_segredo(self, opener):
        self.simular(opener, CHAVE_A)
        response = self.pedido("salvar", chave=CHAVE_A)
        self.assertEqual(response.status_code, 302)
        config = IntegracaoGemini.objects.get(workspace=self.a)
        self.assertEqual(config.provedor, "gemini")
        self.assertNotIn(CHAVE_A.encode(), bytes(config.credencial))
        self.assertEqual(descriptografar(config), CHAVE_A)
        tela = self.client.get(self.url)
        self.assertContains(tela, "Credencial Gemini salva")
        self.assertNotContains(tela, CHAVE_A)
        self.assertTrue(EventoIntegracaoGemini.objects.filter(workspace=self.a, evento="gemini_conectada").exists())

    @patch("apps.leads.gemini.build_opener")
    def test_chave_recusada_preserva_configuracao_anterior(self, opener):
        self.simular(opener, CHAVE_A)
        self.pedido("salvar", chave=CHAVE_A)
        anterior = IntegracaoGemini.objects.get(workspace=self.a)
        self.simular(opener, CHAVE_INVALIDA, rejeitar=True)
        response = self.pedido("salvar", chave=CHAVE_INVALIDA)
        self.assertContains(response, "A chave do Gemini foi recusada")
        atual = IntegracaoGemini.objects.get(workspace=self.a)
        self.assertEqual(bytes(atual.credencial), bytes(anterior.credencial))

    @patch("apps.leads.gemini.build_opener")
    def test_membro_comum_nao_configura_e_outro_workspace_e_isolado(self, opener):
        self.client.force_login(self.member)
        self.selecionar(self.a)
        self.assertEqual(self.pedido("salvar", chave=CHAVE_A).status_code, 403)
        self.assertFalse(IntegracaoGemini.objects.exists())
        self.client.force_login(self.admin)
        self.selecionar(self.b)
        self.simular(opener, "AIza-fixture-empresa-b")
        self.assertEqual(self.pedido("salvar", chave="AIza-fixture-empresa-b").status_code, 302)
        self.assertEqual(IntegracaoGemini.objects.count(), 1)
        self.assertFalse(IntegracaoGemini.objects.filter(workspace=self.a).exists())

    def test_criptografia_autenticada_vincula_workspace_e_rotacao(self):
        cifra = criptografar(self.a.pk, CHAVE_A)
        com_contexto_errado = IntegracaoGemini(workspace=self.b, credencial=cifra, limite_diario=1)
        with self.assertRaises(Exception):
            descriptografar(com_contexto_errado)
        correto = IntegracaoGemini(workspace=self.a, credencial=cifra, limite_diario=1)
        nova = base64.urlsafe_b64encode(b"z" * 32).decode()
        with override_settings(GEMINI_ENCRYPTION_KEYS=f"{nova},{AES_TESTE}"):
            self.assertEqual(descriptografar(correto), CHAVE_A)
