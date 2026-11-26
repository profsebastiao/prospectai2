import base64
import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.gemini_credentials import criptografar, descriptografar
from apps.accounts.models import IntegracaoGemini, Workspace, WorkspaceMembership
from apps.leads import ia
from apps.leads.ia_comum import IAIndisponivel

CHAVE = "sk-or-v1-fixture-empresa"
AES = base64.urlsafe_b64encode(b"o" * 32).decode()


def resposta_http(corpo):
    resposta = MagicMock()
    resposta.read.return_value = json.dumps(corpo).encode()
    contexto = MagicMock()
    contexto.__enter__.return_value = resposta
    return contexto


def ok(conteudo, **mensagem):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": conteudo, **mensagem}}]}


def erro_http(codigo, corpo):
    return HTTPError("https://openrouter.ai", codigo, "x", {}, io.BytesIO(json.dumps(corpo).encode()))


@override_settings(GEMINI_ENCRYPTION_KEYS=AES, OPENROUTER_MODEL="openrouter/free", OPENROUTER_FALLBACK_MODELS="",
                   IA_COMPANY_DAILY_LIMIT=5)
class OpenRouterApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ws = Workspace.objects.create(nome="Empresa O", slug="empresa-o")
        cls.integracao = IntegracaoGemini.objects.create(
            workspace=cls.ws, provedor="openrouter", credencial=criptografar(cls.ws.pk, CHAVE), limite_diario=5,
        )

    def falhar(self, opener, codigo, corpo):
        opener.return_value.open.side_effect = erro_http(codigo, corpo)
        with self.assertRaises(IAIndisponivel) as ctx:
            ia.gerar(self.ws.pk, "teste", {})
        return ctx.exception

    @patch("apps.leads.openrouter.build_opener")
    def test_gerar_usa_chave_da_empresa_e_esquema(self, opener):
        opener.return_value.open.return_value = resposta_http(ok('{"itens": [1]}'))
        esquema = {"type": "object", "properties": {"itens": {"type": "array"}}, "required": ["itens"]}
        self.assertEqual(ia.gerar(self.ws.pk, "Analise.", {"fontes": []}, schema=esquema), {"itens": [1]})
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://openrouter.ai/api/v1/chat/completions")
        cabecalhos = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(cabecalhos["authorization"], f"Bearer {CHAVE}")
        self.assertEqual(cabecalhos["x-title"], "ProspectAI")
        self.assertNotIn(CHAVE.encode(), req.data)
        corpo = json.loads(req.data)
        self.assertEqual(corpo["model"], "openrouter/free")
        self.assertNotIn("models", corpo)
        self.assertEqual(corpo["response_format"]["json_schema"]["schema"], esquema)
        self.assertIn("JSON", corpo["messages"][0]["content"])

    @patch("apps.leads.openrouter.build_opener")
    def test_modelos_reserva_vao_na_mesma_chamada(self, opener):
        opener.return_value.open.return_value = resposta_http(ok('{"ok": true}'))
        with self.settings(OPENROUTER_MODEL="google/gemini-3.6-flash",
                           OPENROUTER_FALLBACK_MODELS="openrouter/free, meta-llama/llama-4-scout:free"):
            ia.gerar(self.ws.pk, "x", {})
        corpo = json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual(corpo["models"], ["google/gemini-3.6-flash", "openrouter/free", "meta-llama/llama-4-scout:free"])

    @patch("apps.leads.openrouter.build_opener")
    def test_aceita_json_com_texto_ou_cercas_em_volta(self, opener):
        opener.return_value.open.return_value = resposta_http(ok('Claro! Aqui está:\n```json\n{"ok": true}\n```'))
        self.assertEqual(ia.gerar(self.ws.pk, "x", {}), {"ok": True})
        opener.return_value.open.return_value = resposta_http(ok('Resultado: {"ok": true} pronto'))
        self.assertEqual(ia.gerar(self.ws.pk, "x", {}), {"ok": True})

    @patch("apps.leads.openrouter.build_opener")
    def test_erros_sao_explicados_sem_vazar_chave(self, opener):
        self.assertEqual(self.falhar(opener, 401, {"error": {"code": 401, "message": "No auth credentials found"}}).codigo, "credencial")
        exc = self.falhar(opener, 402, {"error": {"code": 402, "message": "Insufficient credits"}})
        self.assertEqual(exc.codigo, "credito")
        self.assertIn("Insufficient credits", str(exc))
        exc = self.falhar(opener, 429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}})
        self.assertEqual(exc.codigo, "limite")
        self.assertIn("free-models-per-day", str(exc))
        exc = self.falhar(opener, 400, {"error": {"code": 400, "message": "x-ai/nada is not a valid model ID"}})
        self.assertEqual(exc.codigo, "modelo")
        exc = self.falhar(opener, 403, {"error": {"code": 403, "message": f"Blocked for {CHAVE}"}})
        self.assertEqual(exc.codigo, "bloqueada")
        self.assertNotIn(CHAVE, str(exc))

    @patch("apps.leads.openrouter.build_opener")
    def test_erro_durante_geracao_com_http_200(self, opener):
        opener.return_value.open.return_value = resposta_http(
            {"error": {"code": 502, "message": "Provider returned error", "metadata": {"raw": "upstream overloaded"}}}
        )
        with self.assertRaisesMessage(IAIndisponivel, "upstream overloaded"):
            ia.gerar(self.ws.pk, "x", {})

    @patch("apps.leads.openrouter.build_opener")
    def test_resposta_cortada_e_explicada(self, opener):
        opener.return_value.open.return_value = resposta_http(
            {"choices": [{"finish_reason": "length", "message": {"content": '{"ok": tr'}}]}
        )
        with self.assertRaisesMessage(IAIndisponivel, "inválida ou incompleta"):
            ia.gerar(self.ws.pk, "x", {})

    @patch("apps.leads.openrouter.build_opener")
    def test_timeout_e_rede(self, opener):
        opener.return_value.open.side_effect = TimeoutError("nao registrar segredo")
        with self.assertLogs("apps.leads.openrouter", level="WARNING") as logs:
            with self.assertRaisesMessage(IAIndisponivel, "não respondeu"):
                ia.gerar(self.ws.pk, "x", {})
        self.assertNotIn("nao registrar segredo", " ".join(logs.output))
        opener.return_value.open.side_effect = URLError(Exception("[SSL: CERTIFICATE_VERIFY_FAILED] failed"))
        with self.assertRaisesMessage(IAIndisponivel, "certificado SSL"):
            ia.gerar(self.ws.pk, "x", {})

    @patch("apps.leads.openrouter.build_opener")
    def test_limite_diario_por_empresa(self, opener):
        IntegracaoGemini.objects.filter(pk=self.integracao.pk).update(limite_diario=2)
        opener.return_value.open.return_value = resposta_http(ok('{"ok": true}'))
        ia.gerar(self.ws.pk, "x", {})
        ia.gerar(self.ws.pk, "x", {})
        with self.assertRaisesMessage(IAIndisponivel, "limite diário"):
            ia.gerar(self.ws.pk, "x", {})
        self.assertEqual(opener.return_value.open.call_count, 2)

    @patch("apps.leads.openrouter.build_opener")
    def test_busca_na_web_traz_fontes_com_links(self, opener):
        IntegracaoGemini.objects.filter(pk=self.integracao.pk).update(pesquisa_web=True, modelo_pesquisa="openrouter/free")
        texto = "O Café Aurora atende em João Pessoa e divulga no Instagram."
        opener.return_value.open.return_value = resposta_http(ok(texto, annotations=[
            {"type": "url_citation", "url_citation": {
                "url": "https://www.instagram.com/cafeaurora/", "title": "Café Aurora (@cafeaurora)",
                "content": "Café Aurora · João Pessoa · encomendas pelo WhatsApp", "start_index": 0, "end_index": 20,
            }},
            {"type": "url_citation", "url_citation": {
                "url": "https://cafeaurora.com.br/", "title": "Café Aurora", "start_index": 0, "end_index": len(texto),
            }},
        ]))
        fontes = ia.pesquisar("café João Pessoa", self.ws.pk)
        self.assertEqual([f["url"] for f in fontes], ["https://www.instagram.com/cafeaurora/", "https://cafeaurora.com.br/"])
        self.assertEqual(fontes[0]["origem"], "openrouter")
        self.assertIn("WhatsApp", fontes[0]["content"])
        self.assertIn("João Pessoa", fontes[1]["content"])
        corpo = json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual(corpo["plugins"], [{"id": "web", "max_results": 8}])

    def test_busca_exige_habilitacao(self):
        with self.assertRaises(IAIndisponivel):
            ia.pesquisar("café", self.ws.pk)

    @patch("apps.leads.openrouter.build_opener")
    def test_credencial_antiga_nao_e_usada(self, opener):
        outra = Workspace.objects.create(nome="Empresa Q", slug="empresa-q")
        IntegracaoGemini.objects.create(
            workspace=outra, provedor="gemini", credencial=criptografar(outra.pk, "AIza-antiga"), limite_diario=5,
        )
        with self.assertRaisesMessage(IAIndisponivel, "Conecte o OpenRouter"):
            ia.gerar(outra.pk, "x", {})
        opener.assert_not_called()


@override_settings(GEMINI_ENCRYPTION_KEYS=AES, OPENROUTER_MODEL="openrouter/free", OPENROUTER_FALLBACK_MODELS="")
class TelaIntegracaoOpenRouterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("gestor-o", password="senha-de-teste-123")
        cls.ws = Workspace.objects.create(nome="Empresa T", slug="empresa-t")
        WorkspaceMembership.objects.create(workspace=cls.ws, usuario=cls.user, pode_gerenciar_gemini=True)

    def setUp(self):
        self.client.force_login(self.user)

    @patch("apps.leads.openrouter.build_opener")
    def test_salvar_chave_substitui_credencial_antiga(self, opener):
        IntegracaoGemini.objects.create(
            workspace=self.ws, provedor="groq", credencial=criptografar(self.ws.pk, "gsk_antiga"), limite_diario=7,
        )
        tela = self.client.get(reverse("integracao_ia"))
        self.assertContains(tela, "credencial antiga que não é mais usada")
        self.assertContains(tela, "openrouter.ai/keys")
        opener.return_value.open.return_value = resposta_http(ok('{"ok": true}'))
        resposta = self.client.post(reverse("integracao_ia"), {"acao": "salvar", "chave": CHAVE}, follow=True, secure=True)
        self.assertContains(resposta, "OpenRouter conectado")
        integracao = IntegracaoGemini.objects.get(workspace=self.ws)
        self.assertEqual(integracao.provedor, "openrouter")
        self.assertEqual(integracao.limite_diario, 7)
        self.assertEqual(descriptografar(integracao), CHAVE)

    @patch("apps.leads.openrouter.build_opener")
    def test_chave_recusada_mantem_a_anterior(self, opener):
        IntegracaoGemini.objects.create(
            workspace=self.ws, provedor="groq", credencial=criptografar(self.ws.pk, "gsk_antiga"), limite_diario=7,
        )
        opener.return_value.open.side_effect = erro_http(401, {"error": {"code": 401, "message": "User not found."}})
        resposta = self.client.post(reverse("integracao_ia"), {"acao": "salvar", "chave": "sk-or-errada"}, secure=True)
        self.assertContains(resposta, "foi recusada")
        self.assertEqual(IntegracaoGemini.objects.get(workspace=self.ws).provedor, "groq")
