import base64
import io
import json
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.gemini_credentials import criptografar
from apps.accounts.models import IntegracaoGemini, Workspace
from apps.leads.groq import IAIndisponivel, gerar, pesquisar_groq, reservar_chamada
from apps.leads.models import LimiteGemini


CHAVE_A = "fixture-groq-empresa-a"
CHAVE_B = "fixture-groq-empresa-b"


@override_settings(
    GROQ_MODEL="openai/gpt-oss-20b", GROQ_COMPANY_DAILY_LIMIT=2,
    GEMINI_ENCRYPTION_KEYS=base64.urlsafe_b64encode(b"x" * 32).decode(),
    GROQ_API_KEY="chave-global-nao-usar",
)
class GroqTransportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Workspace.objects.create(nome="Empresa A", slug="empresa-a-groq")
        cls.b = Workspace.objects.create(nome="Empresa B", slug="empresa-b-groq")
        for ws, key in ((cls.a, CHAVE_A), (cls.b, CHAVE_B)):
            IntegracaoGemini.objects.create(
                workspace=ws, provedor="groq", credencial=criptografar(ws.pk, key), limite_diario=2,
            )

    @staticmethod
    def response(opener, body):
        response = MagicMock()
        response.read.return_value = json.dumps(body).encode()
        opener.return_value.open.return_value.__enter__.return_value = response

    @patch("apps.leads.groq.build_opener")
    def test_chat_usa_chave_da_empresa_e_nao_envia_segredo_no_payload(self, opener):
        self.response(opener, {"choices": [{"finish_reason": "stop", "message": {"content": "{\"ok\":true}"}}]})
        self.assertTrue(gerar(self.a.pk, "analisar", {"texto": "evidencia"}, schema={"type": "object"})["ok"])
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.groq.com/openai/v1/chat/completions")
        headers = dict((k.lower(), v) for k, v in req.header_items())
        self.assertEqual(headers["authorization"], f"Bearer {CHAVE_A}")
        self.assertNotIn(b"fixture-groq", req.data)
        self.assertNotIn(b"chave-global-nao-usar", req.data)
        self.assertEqual(LimiteGemini.objects.get(escopo=f"groq:empresa:{self.a.pk}").chamadas, 1)

    @patch("apps.leads.groq.build_opener")
    def test_web_exige_opt_in_e_retorna_somente_fontes_reais(self, opener):
        with self.assertRaises(IAIndisponivel):
            pesquisar_groq("cozinha", self.a.pk)
        opener.assert_not_called()
        IntegracaoGemini.objects.filter(workspace=self.a).update(
            pesquisa_web=True, modelo_pesquisa="openai/gpt-oss-20b",
        )
        self.response(opener, {"status": "completed", "output": [
            {"type": "web_search_call", "action": {"sources": [
                {"url": "https://fonte.example/pedido", "title": "Pedido", "snippet": "Trecho publico"},
                {"url": "javascript:alert(1)", "title": "Ignorar"},
            ]}},
        ]})
        fontes = pesquisar_groq("cozinha em Joao Pessoa", self.a.pk)
        self.assertEqual([x["url"] for x in fontes], ["https://fonte.example/pedido"])
        self.assertEqual(fontes[0]["origem"], "groq")
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.groq.com/openai/v1/responses")
        self.assertEqual(json.loads(req.data)["tools"], [{"type": "browser_search"}])

    def test_cotas_sao_separadas_por_empresa_e_provedor(self):
        LimiteGemini.objects.create(dia=timezone.localdate(), escopo=f"empresa:{self.a.pk}", chamadas=10)
        reservar_chamada(self.a.pk, 1)
        with self.assertRaises(IAIndisponivel):
            reservar_chamada(self.a.pk, 1)
        reservar_chamada(self.b.pk, 1)
        self.assertEqual(LimiteGemini.objects.get(escopo=f"groq:empresa:{self.b.pk}").chamadas, 1)
        self.assertEqual(LimiteGemini.objects.get(escopo=f"empresa:{self.a.pk}").chamadas, 10)

    @override_settings(GROQ_GLOBAL_DAILY_LIMIT=1)
    def test_teto_global_bloqueia_novas_empresas_sem_ultrapassar_o_limite(self):
        reservar_chamada(self.a.pk, 5)
        with self.assertRaises(IAIndisponivel):
            reservar_chamada(self.b.pk, 5)
        self.assertEqual(LimiteGemini.objects.get(escopo="global:groq").chamadas, 1)
        self.assertFalse(LimiteGemini.objects.filter(escopo=f"groq:empresa:{self.b.pk}").exists())

    @patch("apps.leads.groq.build_opener")
    def test_chave_global_nao_e_fallback_e_401_e_classificado(self, opener):
        IntegracaoGemini.objects.filter(workspace=self.a).delete()
        with self.assertRaisesMessage(IAIndisponivel, "Conecte a Groq"):
            gerar(self.a.pk, "teste", {})
        opener.assert_not_called()
        IntegracaoGemini.objects.create(
            workspace=self.a, provedor="groq", credencial=criptografar(self.a.pk, CHAVE_A), limite_diario=2,
        )
        opener.return_value.open.side_effect = HTTPError(
            "https://api.groq.com", 401, "unauthorized", {},
            io.BytesIO(b'{"error":{"code":"invalid_api_key"}}'),
        )
        with self.assertRaisesMessage(IAIndisponivel, "foi recusada"):
            gerar(self.a.pk, "teste", {})

        opener.return_value.open.side_effect = HTTPError(
            "https://api.groq.com", 403, "forbidden", {},
            io.BytesIO(b'{"error":{"code":"model_permission_blocked_project"}}'),
        )
        with self.assertRaises(IAIndisponivel) as erro:
            gerar(self.a.pk, "teste", {})
        self.assertEqual(erro.exception.codigo, "permissao_modelo")
        self.assertIn("permissoes", str(erro.exception))

    @patch("apps.leads.groq.build_opener")
    def test_timeout_nao_expoe_detalhes(self, opener):
        opener.return_value.open.side_effect = TimeoutError("nao registrar segredo")
        with self.assertLogs("apps.leads.groq", level="WARNING") as logs:
            with self.assertRaisesMessage(IAIndisponivel, "temporariamente"):
                gerar(self.a.pk, "teste", {})
        self.assertNotIn("nao registrar segredo", " ".join(logs.output))


@override_settings(
    GROQ_MODEL="openai/gpt-oss-20b", GROQ_COMPANY_DAILY_LIMIT=5,
    GEMINI_ENCRYPTION_KEYS=base64.urlsafe_b64encode(b"x" * 32).decode(),
)
class GroqDiagnosticoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ws = Workspace.objects.create(nome="Empresa D", slug="empresa-d-groq")
        IntegracaoGemini.objects.create(
            workspace=cls.ws, provedor="groq", credencial=criptografar(cls.ws.pk, CHAVE_A), limite_diario=5,
        )

    def erro(self, opener, codigo, corpo):
        opener.return_value.open.side_effect = HTTPError("https://api.groq.com", codigo, "x", {}, io.BytesIO(corpo))
        with self.assertRaises(IAIndisponivel) as ctx:
            gerar(self.ws.pk, "teste", {})
        return ctx.exception

    @patch("apps.leads.groq.build_opener")
    def test_envia_user_agent_proprio(self, opener):
        GroqTransportTests.response(opener, {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
        gerar(self.ws.pk, "teste", {})
        req = opener.return_value.open.call_args.args[0]
        agente = dict((k.lower(), v) for k, v in req.header_items())["user-agent"]
        self.assertTrue(agente.startswith("ProspectAI/"))
        self.assertNotIn(b'"store"', req.data)

    @patch("apps.leads.groq.build_opener")
    def test_pagina_do_cloudflare_vira_bloqueio_de_rede(self, opener):
        exc = self.erro(opener, 403, b"error code: 1010")
        self.assertEqual(exc.codigo, "bloqueio_rede")
        self.assertIn("firewall", str(exc))
        self.assertIn("1010", str(exc))

    @patch("apps.leads.groq.build_opener")
    def test_403_generico_mostra_motivo_da_groq_sem_vazar_chave(self, opener):
        corpo = json.dumps({"error": {"message": f"Forbidden for {CHAVE_A} gsk_abc123", "code": "forbidden"}}).encode()
        exc = self.erro(opener, 403, corpo)
        self.assertEqual(exc.codigo, "acesso_negado")
        self.assertIn("Forbidden", str(exc))
        self.assertNotIn(CHAVE_A, str(exc))
        self.assertNotIn("gsk_abc123", str(exc))

    @patch("apps.leads.groq.build_opener")
    def test_400_de_parametro_nao_e_confundido_com_modelo(self, opener):
        corpo = b'{"error":{"message":"property \'foo\' is unsupported","type":"invalid_request_error"}}'
        exc = self.erro(opener, 400, corpo)
        self.assertEqual(exc.codigo, "requisicao")
        self.assertIn("unsupported", str(exc))

    @patch("apps.leads.groq.build_opener")
    def test_modo_json_inclui_palavra_json_e_esquema(self, opener):
        GroqTransportTests.response(opener, {"choices": [{"finish_reason": "stop", "message": {"content": "{\"itens\":[]}"}}]})
        esquema = {"type": "object", "properties": {"itens": {"type": "array"}}, "required": ["itens"]}
        gerar(self.ws.pk, "Analise as fontes.", {"fontes": []}, schema=esquema)
        payload = json.loads(opener.return_value.open.call_args.args[0].data)
        sistema = payload["messages"][0]["content"]
        self.assertIn("json", sistema.lower())
        self.assertIn('"required": ["itens"]', sistema)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
