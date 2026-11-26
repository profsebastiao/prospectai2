import base64
import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.gemini_credentials import CredencialIndisponivel, criptografar, descriptografar, descriptografar_busca
from apps.accounts.models import IntegracaoGemini, Workspace, WorkspaceMembership
from apps.leads.models import LimiteGemini, PesquisaProspeccao
from apps.leads.serper import fontes_da_resposta
from apps.services.models import Servico

AES = base64.urlsafe_b64encode(b"s" * 32).decode()
CHAVE_IA = "sk-or-v1-fixture"
CHAVE_BUSCA = "serper-fixture-0123456789"

RESPOSTA = {
    "knowledgeGraph": {
        "title": "Ótica Virtuosa", "type": "Ótica", "rating": 5.0, "ratingCount": 15,
        "attributes": {"Endereço": "Av. Visc. de Pelotas, 162 - Centro, João Pessoa - PB", "Telefone": "(83) 98887-9222"},
    },
    "places": [{"title": "Ótica Virtuosa", "category": "Ótica", "rating": 5, "ratingCount": 15,
                "address": "Av. Visc. de Pelotas, 162", "phoneNumber": "(83) 98887-9222", "cid": "123456789012"}],
    "organic": [
        {"title": "Ótica Virtuosa (@oticavirtuosa_) • Instagram", "link": "https://www.instagram.com/oticavirtuosa_/",
         "snippet": "1.548 seguidores · Exames, lentes, óculos e armações. João Pessoa-PB."},
        {"title": "Sem trecho", "link": "https://exemplo.com/"},
    ],
    "credits": 1,
}


def http(corpo):
    resposta = MagicMock()
    resposta.read.return_value = json.dumps(corpo).encode()
    contexto = MagicMock()
    contexto.__enter__.return_value = resposta
    return contexto


class FontesSerperTests(TestCase):
    def test_converte_quadro_maps_e_resultados_em_evidencias(self):
        fontes = fontes_da_resposta(RESPOSTA)
        self.assertEqual(len(fontes), 3)
        grafico, local, instagram = fontes
        self.assertIn("Nota 5,0 (15) no Google", grafico["content"])
        self.assertIn("(83) 98887-9222", grafico["content"])
        self.assertEqual(local["url"], "https://maps.google.com/?cid=123456789012")
        self.assertEqual(instagram["url"], "https://www.instagram.com/oticavirtuosa_/")
        self.assertTrue(all(f["origem"] == "google" for f in fontes))


@override_settings(GEMINI_ENCRYPTION_KEYS=AES, OPENROUTER_MODEL="openrouter/free", SERPER_DAILY_LIMIT=4)
class BuscaGoogleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("gestor-s", password="senha-de-teste-123")
        cls.ws = Workspace.objects.create(nome="SdevBR", slug="sdevbr-s", ramo_prospeccao="Agência de marketing",
                                          publico_prospeccao="Óticas", regiao_prospeccao="João Pessoa - PB")
        WorkspaceMembership.objects.create(workspace=cls.ws, usuario=cls.user, papel="gestor", pode_gerenciar_gemini=True)
        cls.servico = Servico.objects.create(workspace=cls.ws, nome="Landing Page")
        cls.integracao = IntegracaoGemini.objects.create(
            workspace=cls.ws, provedor="openrouter", credencial=criptografar(cls.ws.pk, CHAVE_IA), limite_diario=10,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def conectar(self):
        IntegracaoGemini.objects.filter(pk=self.integracao.pk).update(
            busca_credencial=criptografar(self.ws.pk, CHAVE_BUSCA, "serper"),
        )

    @patch("apps.leads.serper.build_opener")
    def test_salvar_chave_de_busca_criptografada_e_separada(self, opener):
        opener.return_value.open.return_value = http({"organic": [], "credits": 1})
        tela = self.client.get(reverse("integracao_ia"))
        self.assertContains(tela, "Busca no Google (Serper.dev)")
        resposta = self.client.post(reverse("integracao_ia"), {"acao": "busca_salvar", "chave_busca": CHAVE_BUSCA},
                                    follow=True, secure=True)
        self.assertContains(resposta, "Busca no Google conectada")
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(req.get_header("X-api-key"), CHAVE_BUSCA)
        integracao = IntegracaoGemini.objects.get(pk=self.integracao.pk)
        self.assertNotIn(CHAVE_BUSCA.encode(), bytes(integracao.busca_credencial))
        self.assertEqual(descriptografar_busca(integracao), CHAVE_BUSCA)
        self.assertEqual(descriptografar(integracao), CHAVE_IA)
        trocada = IntegracaoGemini(workspace=self.ws, credencial=integracao.busca_credencial, limite_diario=1)
        with self.assertRaises(CredencialIndisponivel):
            descriptografar(trocada)

    @patch("apps.leads.serper.build_opener")
    def test_chave_de_busca_recusada_nao_e_salva(self, opener):
        opener.return_value.open.side_effect = HTTPError("https://google.serper.dev", 403, "x", {},
                                                         io.BytesIO(b'{"message":"Unauthorized."}'))
        resposta = self.client.post(reverse("integracao_ia"), {"acao": "busca_salvar", "chave_busca": "chave-errada-123"},
                                    secure=True)
        self.assertContains(resposta, "foi recusada")
        self.assertIsNone(IntegracaoGemini.objects.get(pk=self.integracao.pk).busca_credencial)

    @patch("config.prospeccao_views.analisar_ia", return_value={"oportunidades": []})
    @patch("apps.leads.serper.build_opener")
    def test_buscar_evidencias_de_uma_consulta(self, opener, ia):
        self.conectar()
        opener.return_value.open.return_value = http(RESPOSTA)
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        self.assertContains(self.client.get(url), "Buscar no Google automaticamente")
        resposta = self.client.post(url, {
            "criterios-ramo": "Agência de marketing", "criterios-publico": "Óticas",
            "criterios-regiao": "João Pessoa - PB", "criterios-servicos": [self.servico.pk],
            "consulta_google": "ótica virtuosa joão pessoa",
            "evidencias-TOTAL_FORMS": "0", "evidencias-INITIAL_FORMS": "0",
        })
        pesquisa = PesquisaProspeccao.objects.get(workspace=self.ws)
        self.assertRedirects(resposta, reverse("prospeccao_pesquisa", args=[pesquisa.pk]))
        self.assertEqual(len(pesquisa.fontes), 3)
        self.assertEqual({f["origem"] for f in pesquisa.fontes}, {"google"})
        corpo = json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual(corpo["q"], "ótica virtuosa joão pessoa")
        self.assertEqual(corpo["gl"], "br")
        self.assertEqual(LimiteGemini.objects.get(escopo=f"busca:empresa:{self.ws.pk}").chamadas, 1)
        self.assertTrue(ia.called)

    @patch("apps.leads.serper.build_opener")
    def test_limite_diario_de_buscas(self, opener):
        self.conectar()
        from django.utils import timezone
        LimiteGemini.objects.create(dia=timezone.localdate(), escopo=f"busca:empresa:{self.ws.pk}", chamadas=4)
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        resposta = self.client.post(url, {
            "criterios-ramo": "Agência de marketing", "criterios-publico": "Óticas",
            "criterios-regiao": "João Pessoa - PB", "criterios-servicos": [self.servico.pk],
            "acao": "google", "evidencias-TOTAL_FORMS": "0", "evidencias-INITIAL_FORMS": "0",
        }, follow=True)
        self.assertContains(resposta, "limite diário de buscas")
        opener.assert_not_called()

    def test_sem_busca_conectada_avisa(self):
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        self.assertNotContains(self.client.get(url), "Buscar no Google automaticamente")
        resposta = self.client.post(url, {
            "criterios-ramo": "Agência de marketing", "criterios-publico": "Óticas",
            "criterios-regiao": "João Pessoa - PB", "criterios-servicos": [self.servico.pk],
            "acao": "google", "evidencias-TOTAL_FORMS": "0", "evidencias-INITIAL_FORMS": "0",
        })
        self.assertContains(resposta, "Conecte a busca no Google")
