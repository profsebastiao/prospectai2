import base64
import io
import json
from email.message import Message
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.gemini_credentials import criptografar, descriptografar
from apps.accounts.models import IntegracaoGemini, Workspace, WorkspaceMembership
from apps.leads import ia
from apps.leads.ia_comum import IAIndisponivel

CHAVE = "AIza-fixture-gemini-empresa"
AES = base64.urlsafe_b64encode(b"g" * 32).decode()


def resposta_http(corpo):
    resposta = MagicMock()
    resposta.read.return_value = json.dumps(corpo).encode()
    contexto = MagicMock()
    contexto.__enter__.return_value = resposta
    return contexto


def ok(texto, **extra):
    candidato = {"content": {"parts": [{"text": "pensando...", "thought": True}, {"text": texto}]}, "finishReason": "STOP"}
    candidato.update(extra)
    return {"candidates": [candidato]}


@override_settings(GEMINI_ENCRYPTION_KEYS=AES, GEMINI_MODEL="gemini-3.8-flash", IA_COMPANY_DAILY_LIMIT=5)
class GeminiApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ws = Workspace.objects.create(nome="Empresa G", slug="empresa-g")
        cls.integracao = IntegracaoGemini.objects.create(
            workspace=cls.ws, provedor="gemini", credencial=criptografar(cls.ws.pk, CHAVE), limite_diario=5,
        )

    def erro(self, opener, codigo, corpo):
        opener.return_value.open.side_effect = HTTPError("https://x", codigo, "x", {}, io.BytesIO(json.dumps(corpo).encode()))
        with self.assertRaises(IAIndisponivel) as ctx:
            ia.gerar(self.ws.pk, "teste", {})
        return ctx.exception

    @patch("apps.leads.gemini.build_opener")
    def test_gerar_usa_chave_da_empresa_esquema_e_ignora_pensamento(self, opener):
        opener.return_value.open.return_value = resposta_http(ok('{"itens": [1]}'))
        esquema = {"type": "object", "properties": {"itens": {"type": "array"}}, "required": ["itens"]}
        self.assertEqual(ia.gerar(self.ws.pk, "Analise.", {"fontes": []}, schema=esquema), {"itens": [1]})
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(
            req.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        )
        cabecalhos = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(cabecalhos["x-goog-api-key"], CHAVE)
        self.assertTrue(cabecalhos["user-agent"].startswith("ProspectAI/"))
        self.assertNotIn(CHAVE.encode(), req.data)
        corpo = json.loads(req.data)
        self.assertEqual(corpo["generationConfig"]["responseFormat"]["text"]["schema"], esquema)
        self.assertIn("JSON", corpo["systemInstruction"]["parts"][0]["text"])

    @patch("apps.leads.gemini.build_opener")
    def test_aceita_json_entre_cercas(self, opener):
        opener.return_value.open.return_value = resposta_http(ok('```json\n{"ok": true}\n```'))
        self.assertEqual(ia.gerar(self.ws.pk, "x", {}), {"ok": True})

    @patch("apps.leads.gemini.build_opener")
    def test_erros_do_google_sao_explicados(self, opener):
        exc = self.erro(opener, 400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                               "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})
        self.assertEqual(exc.codigo, "credencial")
        exc = self.erro(opener, 429, {"error": {"message": "Quota exceeded for metric", "status": "RESOURCE_EXHAUSTED"}})
        self.assertEqual(exc.codigo, "limite")
        self.assertIn("Quota exceeded", str(exc))
        exc = self.erro(opener, 404, {"error": {"message": "models/x is not found", "status": "NOT_FOUND"}})
        self.assertEqual(exc.codigo, "modelo")
        exc = self.erro(opener, 403, {"error": {"message": f"Denied for {CHAVE}", "status": "PERMISSION_DENIED"}})
        self.assertEqual(exc.codigo, "acesso_negado")
        self.assertNotIn(CHAVE, str(exc))

    @patch("apps.leads.gemini.build_opener")
    def test_resposta_bloqueada(self, opener):
        opener.return_value.open.return_value = resposta_http({"promptFeedback": {"blockReason": "SAFETY"}})
        with self.assertRaisesMessage(IAIndisponivel, "bloqueou"):
            ia.gerar(self.ws.pk, "x", {})

    @patch("apps.leads.gemini.build_opener")
    def test_busca_no_google_resolve_links_reais_e_traz_sugestoes(self, opener):
        IntegracaoGemini.objects.filter(pk=self.integracao.pk).update(pesquisa_web=True, modelo_pesquisa="gemini-3.8-flash")
        redirecionamento = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
        geracao = ok("Resumo.", groundingMetadata={
            "groundingChunks": [{"web": {"uri": redirecionamento, "title": "instagram.com"}}],
            "groundingSupports": [{"segment": {"text": "Café Aurora em João Pessoa."}, "groundingChunkIndices": [0]}],
            "searchEntryPoint": {"renderedContent": "<div>sugestões</div>"},
        })

        def abrir(req, timeout=None):
            if req.full_url == redirecionamento:
                cabecalhos = Message()
                cabecalhos["Location"] = "https://www.instagram.com/cafeaurora/"
                raise HTTPError(req.full_url, 302, "Found", cabecalhos, io.BytesIO(b""))
            return resposta_http(geracao)

        opener.return_value.open.side_effect = abrir
        resultados = ia.pesquisar("café João Pessoa", self.ws.pk)
        fonte = resultados[0]
        self.assertEqual(fonte["url"], "https://www.instagram.com/cafeaurora/")
        self.assertEqual(fonte["origem"], "gemini")
        self.assertIn("Café Aurora", fonte["content"])
        self.assertEqual(resultados[-1], {"widget_google": "<div>sugestões</div>"})
        corpo = json.loads(opener.return_value.open.call_args_list[0].args[0].data)
        self.assertEqual(corpo["tools"], [{"google_search": {}}])

    def test_busca_exige_habilitacao(self):
        with self.assertRaises(IAIndisponivel):
            ia.pesquisar("café", self.ws.pk)

    @patch("apps.leads.gemini.build_opener")
    def test_credencial_antiga_nao_e_usada(self, opener):
        outra = Workspace.objects.create(nome="Empresa Q", slug="empresa-q")
        IntegracaoGemini.objects.create(
            workspace=outra, provedor="groq", credencial=criptografar(outra.pk, "gsk_fixture"), limite_diario=5,
        )
        with self.assertRaisesMessage(IAIndisponivel, "Conecte o Gemini"):
            ia.gerar(outra.pk, "x", {})
        opener.assert_not_called()

    @patch("apps.leads.gemini.build_opener")
    def test_limite_diario_por_empresa(self, opener):
        IntegracaoGemini.objects.filter(pk=self.integracao.pk).update(limite_diario=2)
        opener.return_value.open.return_value = resposta_http(ok('{"ok": true}'))
        ia.gerar(self.ws.pk, "x", {})
        ia.gerar(self.ws.pk, "x", {})
        with self.assertRaisesMessage(IAIndisponivel, "limite diário"):
            ia.gerar(self.ws.pk, "x", {})
        self.assertEqual(opener.return_value.open.call_count, 2)

    @patch("apps.leads.gemini.build_opener")
    def test_timeout_nao_expoe_detalhes(self, opener):
        opener.return_value.open.side_effect = TimeoutError("nao registrar segredo")
        with self.assertLogs("apps.leads.gemini", level="WARNING") as logs:
            with self.assertRaisesMessage(IAIndisponivel, "temporariamente"):
                ia.gerar(self.ws.pk, "x", {})
        self.assertNotIn("nao registrar segredo", " ".join(logs.output))


@override_settings(GEMINI_ENCRYPTION_KEYS=AES, GEMINI_MODEL="gemini-3.8-flash")
class TelaIntegracaoGeminiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("gestor-g", password="senha-de-teste-123")
        cls.ws = Workspace.objects.create(nome="Empresa T", slug="empresa-t")
        WorkspaceMembership.objects.create(workspace=cls.ws, usuario=cls.user, pode_gerenciar_gemini=True)

    def setUp(self):
        self.client.force_login(self.user)

    @patch("apps.leads.gemini.build_opener")
    def test_salvar_chave_gemini_substitui_a_groq(self, opener):
        IntegracaoGemini.objects.create(
            workspace=self.ws, provedor="groq", credencial=criptografar(self.ws.pk, "gsk_antiga"), limite_diario=7,
        )
        tela = self.client.get(reverse("integracao_ia"))
        self.assertContains(tela, "credencial antiga que não é mais usada")
        self.assertContains(tela, "aistudio.google.com")
        opener.return_value.open.return_value = resposta_http(ok('{"ok": true}'))
        resposta = self.client.post(reverse("integracao_ia"), {"acao": "salvar", "chave": CHAVE}, follow=True, secure=True)
        self.assertContains(resposta, "Gemini conectado")
        integracao = IntegracaoGemini.objects.get(workspace=self.ws)
        self.assertEqual(integracao.provedor, "gemini")
        self.assertEqual(integracao.limite_diario, 7)
        self.assertEqual(descriptografar(integracao), CHAVE)

    @patch("apps.leads.gemini.build_opener")
    def test_chave_recusada_mantem_a_anterior(self, opener):
        IntegracaoGemini.objects.create(
            workspace=self.ws, provedor="groq", credencial=criptografar(self.ws.pk, "gsk_antiga"), limite_diario=7,
        )
        opener.return_value.open.side_effect = HTTPError("https://x", 400, "x", {}, io.BytesIO(
            b'{"error":{"message":"API key not valid.","status":"INVALID_ARGUMENT","details":[{"reason":"API_KEY_INVALID"}]}}'
        ))
        resposta = self.client.post(reverse("integracao_ia"), {"acao": "salvar", "chave": "AIza-errada"}, secure=True)
        self.assertContains(resposta, "foi recusada")
        self.assertEqual(IntegracaoGemini.objects.get(workspace=self.ws).provedor, "groq")
