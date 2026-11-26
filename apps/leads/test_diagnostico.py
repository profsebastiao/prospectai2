from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Workspace, WorkspaceMembership
from apps.leads.diagnostico import diagnosticar, nome_da_fonte, texto_relatorio
from apps.leads.ia_comum import IAIndisponivel
from apps.leads.models import AnaliseProspeccao, PesquisaProspeccao, RelatorioProspeccao

ANUNCIO = (
    "🚗 PELÍCULA WINDOW PREMIUM: o máximo em proteção e conforto para o seu carro!\n"
    "Siga @evolusom.jp\n📲 Agende já a sua: (83) 99611-2970\n📍 Av. Frei Agostinho, 280 – Mandacaru"
)
SERVICOS = [{"id": 1, "nome": "Instagram"}, {"id": 2, "nome": "Landing Page"}, {"id": 3, "nome": "Google Meu Negócio"}]


def fonte(conteudo=ANUNCIO, url="", titulo=""):
    return {"id": 1, "url": url, "title": titulo, "content": conteudo, "origem": "manual", "restrita": False, "publicacao": ""}


def candidato_manual(texto=ANUNCIO):
    return {"fonte": "", "fonte_id": 1, "nome": "", "descricao": texto, "cidade": "João Pessoa - PB",
            "perfis": [], "links": [], "email": "", "telefone": "", "whatsapp": ""}


class DiagnosticoTests(TestCase):
    def test_extrai_dados_e_aponta_o_que_falta(self):
        d = diagnosticar(candidato_manual(), [fonte()], SERVICOS, "João Pessoa - PB")
        self.assertEqual(d["empresa"]["telefones"], ["(83) 99611-2970"])
        self.assertEqual(d["empresa"]["enderecos"], ["Av. Frei Agostinho, 280 – Mandacaru"])
        situacao = {c["codigo"]: c["situacao"] for c in d["canais"]}
        self.assertEqual(situacao["instagram"], "mencionado")
        self.assertEqual(situacao["whatsapp"], "mencionado")
        self.assertEqual(situacao["site"], "nao")
        perdas = {p["codigo"]: p for p in d["perdas"]}
        self.assertEqual(set(perdas), {"facebook", "google", "site", "landing"})
        self.assertEqual(perdas["landing"]["servico"], "Landing Page")
        self.assertEqual(perdas["site"]["servico"], "")
        self.assertEqual(d["pontuacao"], {"encontrados": 2, "total": 6})
        relatorio = texto_relatorio(d)
        self.assertIn("O QUE A EMPRESA ESTÁ DEIXANDO DE GANHAR", relatorio)
        self.assertIn("Como resolver: Landing Page.", relatorio)
        self.assertTrue(relatorio.endswith("confirme antes de apresentar ao cliente."))

    def test_links_viram_canais_encontrados(self):
        texto = (
            "Loja Sol Som - som automotivo em João Pessoa. https://www.instagram.com/solsom/ "
            "https://facebook.com/solsomjp https://wa.me/5583999990000 https://maps.app.goo.gl/abc123"
        )
        cand = candidato_manual(texto)
        cand["links"] = [{"url": "https://solsom.com.br/", "tipo": "Site do negócio", "fonte": "", "evidencia": ""}]
        d = diagnosticar(cand, [fonte(texto)], SERVICOS)
        situacao = {c["codigo"]: c["situacao"] for c in d["canais"]}
        for codigo in ("instagram", "facebook", "whatsapp", "google", "site"):
            self.assertEqual(situacao[codigo], "encontrado", codigo)
        self.assertEqual([p["codigo"] for p in d["perdas"]], ["landing"])

    def test_nome_da_fonte_usa_primeira_linha_sem_emoji(self):
        self.assertEqual(nome_da_fonte(fonte(), "x"), "PELÍCULA WINDOW PREMIUM: o máximo em proteção e conforto para o seu carro!")
        self.assertEqual(nome_da_fonte(fonte(titulo="Evolusom"), "x"), "Evolusom")
        self.assertEqual(nome_da_fonte(fonte(conteudo=""), "Evidência 1"), "Evidência 1")


class RelatorioOportunidadeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("gestor-r", password="senha-de-teste-123")
        cls.ws = Workspace.objects.create(nome="SdevBR", slug="sdevbr-r")
        WorkspaceMembership.objects.create(workspace=cls.ws, usuario=cls.user, papel="gestor")
        f = fonte(titulo="Evolusom")
        cls.pesquisa = PesquisaProspeccao.objects.create(
            workspace=cls.ws, modo="oportunidades", consulta="João Pessoa - PB",
            criterios={"empresa": "SdevBR", "regiao": "João Pessoa - PB", "servicos": SERVICOS},
            fontes=[f], chave="teste-relatorio",
            resultados=[{"tipo": "cliente", "classificacao": "Possível cliente", "nome": "Evolusom", "fonte": f,
                         "servico": "Instagram", "regiao": "João Pessoa - PB", "compatibilidade": 1,
                         "evidencia": "Av. Frei Agostinho, 280 – Mandacaru", "texto": "Loja de películas."}],
        )

    def setUp(self):
        self.client.force_login(self.user)

    @patch("apps.leads.prospeccao.analisar_ia", return_value={"achados": [], "recomendacoes": []})
    def test_gera_diagnostico_relatorio_e_download(self, ia):
        url = reverse("prospeccao_pesquisa", args=[self.pesquisa.pk])
        self.assertContains(self.client.get(url), "Gerar relatório da empresa")
        resposta = self.client.post(url, {"relatorio": "0"})
        analise = AnaliseProspeccao.objects.get(pesquisa=self.pesquisa)
        self.assertRedirects(resposta, reverse("prospeccao_analise", args=[analise.pk]))
        tela = self.client.get(reverse("prospeccao_analise", args=[analise.pk]))
        self.assertContains(tela, "O que a empresa está deixando de ganhar")
        self.assertContains(tela, "(83) 99611-2970")
        self.assertContains(tela, "2 de 6 canais essenciais")
        self.client.post(reverse("prospeccao_analise", args=[analise.pk]))
        relatorio = RelatorioProspeccao.objects.get(analise=analise)
        self.assertTrue(relatorio.titulo.startswith("Diagnóstico de presença digital: Evolusom"))
        self.assertIn("DADOS DA EMPRESA", relatorio.achados)
        self.assertIn("Sem site próprio", relatorio.achados)
        # Clicar de novo reabre a mesma análise, sem nova chamada de IA.
        self.client.post(url, {"relatorio": "0"})
        self.assertEqual(ia.call_count, 1)

    @patch("apps.leads.prospeccao.analisar_ia", side_effect=IAIndisponivel("Limite atingido."))
    def test_diagnostico_sai_mesmo_sem_ia(self, ia):
        self.client.post(reverse("prospeccao_pesquisa", args=[self.pesquisa.pk]), {"relatorio": "0"})
        analise = AnaliseProspeccao.objects.get(pesquisa=self.pesquisa)
        self.assertIn("feito sem a IA", analise.dados["aviso"])
        self.assertTrue(analise.dados["diagnostico"]["perdas"])

    def test_indice_invalido(self):
        resposta = self.client.post(reverse("prospeccao_pesquisa", args=[self.pesquisa.pk]), {"relatorio": "9"})
        self.assertEqual(resposta.status_code, 400)


class DiagnosticoLinktreeTests(TestCase):
    def test_links_do_linktree_nao_viram_site_citado(self):
        texto = (
            "https://www.google.com/maps/place/Evolusom/@-7.1,-34.8,17z\n"
            "https://api.whatsapp.com/send/?phone=5583996112970&text&type=phone_number\n"
            "https://www.instagram.com/equipadoraevolusom/"
        )
        d = diagnosticar(candidato_manual(texto), [fonte(texto)], SERVICOS)
        situacao = {c["codigo"]: c["situacao"] for c in d["canais"]}
        self.assertEqual(situacao["instagram"], "encontrado")
        self.assertEqual(situacao["whatsapp"], "encontrado")
        self.assertEqual(situacao["google"], "encontrado")
        self.assertEqual(situacao["site"], "nao")
        self.assertEqual({p["codigo"] for p in d["perdas"]}, {"facebook", "site", "landing"})


    def test_wa_link_conta_como_whatsapp(self):
        texto = "ÓTICA VIRTUOSA | ÓTICA EM JOÃO PESSOA. Exames, lentes, óculos e armações. Entre em contato conosco. wa.link/bgsec1"
        d = diagnosticar(candidato_manual(texto), [fonte(texto, url="https://www.instagram.com/oticavirtuosa_/")], SERVICOS)
        situacao = {c["codigo"]: c["situacao"] for c in d["canais"]}
        self.assertEqual(situacao["whatsapp"], "encontrado")
        self.assertEqual(situacao["instagram"], "encontrado")


class RelatorioTodasEvidenciasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("gestor-t", password="senha-de-teste-123")
        cls.ws = Workspace.objects.create(nome="SdevBR", slug="sdevbr-t")
        WorkspaceMembership.objects.create(workspace=cls.ws, usuario=cls.user, papel="gestor")
        f1 = {"id": 1, "url": "https://instagram.com/oticavirtuosa_", "title": "Instagram da Ótica Virtuosa",
              "content": "Exames, lentes, óculos e armações. João Pessoa-PB. Link na bio: wa.link/bgsec1",
              "origem": "manual", "restrita": False, "publicacao": ""}
        f2 = {"id": 2, "url": "", "title": "",
              "content": "Ótica Virtuosa – (83) 98875-4300 – Av. Visconde de Pelotas, 162 – Centro – João Pessoa",
              "origem": "manual", "restrita": False, "publicacao": ""}
        cls.pesquisa = PesquisaProspeccao.objects.create(
            workspace=cls.ws, modo="oportunidades", consulta="João Pessoa - PB", chave="teste-todas",
            criterios={"empresa": "SdevBR", "regiao": "João Pessoa - PB", "servicos": SERVICOS}, fontes=[f1, f2],
            resultados=[
                {"tipo": "cliente", "classificacao": "Possível cliente", "nome": "Ótica", "fonte": f2, "servico": "Landing Page",
                 "regiao": "", "compatibilidade": 1, "evidencia": "", "texto": ""},
                {"tipo": "parceiro", "classificacao": "Possível parceiro", "nome": "Instagram da Ótica Virtuosa", "fonte": f1,
                 "servico": "Instagram", "regiao": "", "compatibilidade": 1, "evidencia": "", "texto": ""},
            ],
        )

    def setUp(self):
        self.client.force_login(self.user)

    @patch("apps.leads.prospeccao.analisar_ia", return_value={"achados": [], "recomendacoes": []})
    def test_relatorio_unico_junta_todas_as_evidencias(self, ia):
        url = reverse("prospeccao_pesquisa", args=[self.pesquisa.pk])
        self.assertContains(self.client.get(url), "Gerar relatório único com todas as evidências")
        self.client.post(url, {"relatorio": "todas"})
        analise = AnaliseProspeccao.objects.get(pesquisa=self.pesquisa)
        self.assertEqual(analise.nome_lead, "Ótica Virtuosa")
        d = analise.dados["diagnostico"]
        situacao = {c["codigo"]: c["situacao"] for c in d["canais"]}
        self.assertEqual(situacao["instagram"], "encontrado")
        self.assertEqual(situacao["whatsapp"], "encontrado")
        self.assertIn("(83) 98875-4300", d["empresa"]["telefones"])
        self.assertTrue(d["empresa"]["enderecos"])
        self.assertEqual(len(analise.dados["fontes"]), 2)
        self.assertEqual(len(ia.call_args.args[2]), 2)


class DiagnosticoAjustesTests(TestCase):
    def test_celular_conta_como_whatsapp_citado_e_nome_fica_limpo(self):
        texto = "Ótica Virtuosa – (83) 98875-4300 – @oticavirtuosa_ Av. Visconde de Pelotas, 162 – Centro – João Pessoa"
        cand = candidato_manual(texto)
        cand["nome"] = "Ótica Virtuosa – (83) 98875-4300 – @oticavirtuosa_"
        d = diagnosticar(cand, [fonte(texto)], SERVICOS)
        situacao = {c["codigo"]: c["situacao"] for c in d["canais"]}
        self.assertEqual(situacao["whatsapp"], "mencionado")
        self.assertEqual(d["empresa"]["nome"], "Ótica Virtuosa")
        self.assertEqual(d["empresa"]["descricao"].count("Visconde de Pelotas"), 1)
        self.assertEqual(nome_da_fonte(fonte(texto)), "Ótica Virtuosa")

    def test_telefone_fixo_sem_mencao_nao_vira_whatsapp(self):
        texto = "Loja Centro. Telefone fixo (83) 3222-1010. Rua das Trincheiras, 100 – João Pessoa"
        d = diagnosticar(candidato_manual(texto), [fonte(texto)], SERVICOS)
        situacao = {c["codigo"]: c["situacao"] for c in d["canais"]}
        self.assertEqual(situacao["whatsapp"], "nao")


    def test_google_maps_nao_reivindicado_vira_argumento(self):
        texto = (
            "Ótica virtuosa 5,0 ★★★★★ (15) Ótica. Av. Visc. de Pelotas, 162 - Centro, João Pessoa - PB, 58013-000. "
            "(83) 98887-9222. Reivindicar esta empresa"
        )
        f = fonte(texto, url="https://www.google.com/maps/place/%C3%93tica+virtuosa/@-7.1027977,-34.9102095,14z")
        d = diagnosticar(candidato_manual(texto), [f], SERVICOS)
        google = next(c for c in d["canais"] if c["codigo"] == "google")
        self.assertEqual(google["situacao"], "encontrado")
        valores = [i["valor"] for i in google["itens"]]
        self.assertIn("Nota 5,0 · 15 avaliações", valores)
        self.assertIn("Perfil não reivindicado pelo dono", valores)
        self.assertEqual(d["perdas"][0]["codigo"], "google_reivindicar")
        self.assertEqual(d["perdas"][0]["servico"], "Google Meu Negócio")
        self.assertNotIn("google", [p["codigo"] for p in d["perdas"]])
        self.assertIn("Google Meu Negócio sem dono", texto_relatorio(d))
