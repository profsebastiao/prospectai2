from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Workspace, WorkspaceMembership
from apps.leads.diagnostico import limpar_nome
from apps.leads.models import AnaliseProspeccao, PesquisaProspeccao
from apps.leads.prospeccao import consultas_google, filtrar_concorrentes, mesma_empresa

CRITERIOS = {"ramo": "Agência de marketing digital e desenvolvimento web", "publico": "Óticas e lojas de óculos",
             "regiao": "João Pessoa - PB", "servicos": [{"id": 1, "nome": "Landing Page"}, {"id": 2, "nome": "Google Meu Negócio"}]}


def f(i, titulo, url, conteudo, origem="google"):
    return {"id": i, "title": titulo, "url": url, "content": conteudo, "origem": origem, "restrita": False, "publicacao": ""}


VIRTUOSA_IG = f(1, "oticavirtuosa_", "https://instagram.com/oticavirtuosa_",
                "ÓTICA VIRTUOSA | ÓTICA EM JOÃO PESSOA. Exames, lentes, óculos e armações. wa.link/bgsec1", "manual")
SEU_OCULOS = f(2, "Ótica Seu Óculos (@oticasseuoculos)", "https://instagram.com/oticasseuoculos",
               "Estamos em João Pessoa e Santa Rita. (83) 99955-8200")
AGENCIA = f(3, "Agência de Marketing em João Pessoa (@agencianweb)", "https://instagram.com/agencianweb",
            "Criação de sites e landing pages com SEO. A Agência Nweb é uma agência de marketing digital.")
KL = f(4, "Agência de Marketing Digital em João Pessoa - PB | KL", "https://klmarketing.com.br/agencia",
       "Marketing digital, tráfego pago e criação de sites em João Pessoa.")
MAPS_VIRTUOSA = f(5, "Ótica virtuosa", "https://maps.google.com/?cid=123456789012",
                  "Google Maps: Ótica virtuosa. Ótica. Nota 5,0 (15) no Google. Av. Visc. de Pelotas, 162. Telefone: (83) 98887-9222")
PARAIBA = f(6, "João Pessoa (@paraibaoculosjp) - Paraíba Óculos", "https://instagram.com/paraibaoculosjp",
            "Atendimento presencial, a domicílio ou online.")


class BuscaEmpresasTests(TestCase):
    def test_consultas_usam_o_perfil_de_cliente_e_nao_o_ramo(self):
        consultas = consultas_google(CRITERIOS)
        self.assertTrue(all("Óticas e lojas de óculos" in c and "João Pessoa - PB" in c for c in consultas))
        self.assertFalse(any("marketing" in c.lower() or "agência" in c.lower() for c in consultas))

    def test_filtro_tira_agencias_e_mantem_oticas(self):
        mantidas = filtrar_concorrentes([VIRTUOSA_IG, SEU_OCULOS, AGENCIA, KL, MAPS_VIRTUOSA, PARAIBA], CRITERIOS)
        self.assertEqual([x["id"] for x in mantidas], [1, 2, 5, 6])

    def test_mesma_empresa_junta_so_a_virtuosa(self):
        fontes = [VIRTUOSA_IG, SEU_OCULOS, AGENCIA, MAPS_VIRTUOSA, PARAIBA]
        self.assertEqual([x["id"] for x in mesma_empresa(VIRTUOSA_IG, fontes, "Ótica Virtuosa")], [1, 5])
        self.assertEqual([x["id"] for x in mesma_empresa(SEU_OCULOS, fontes, "Ótica Seu Óculos")], [2])

    def test_nomes_limpos(self):
        self.assertEqual(limpar_nome("João Pessoa (@paraibaoculosjp) - Paraíba Óculos", "João Pessoa - PB"), "Paraíba Óculos")
        self.assertEqual(limpar_nome("Mello Óculos | ÓTICA (@mellooculoss) • Instagram photos and videos"), "Mello Óculos")
        self.assertEqual(limpar_nome("Ótica Prime Vision (@oticaprimevisionjp) · João Pessoa, PB"), "Ótica Prime Vision")
        self.assertEqual(limpar_nome("Ótica Virtuosa – (83) 98875-4300 – @oticavirtuosa_"), "Ótica Virtuosa")


class RelatorioPorEmpresaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("gestor-e", password="senha-de-teste-123")
        cls.ws = Workspace.objects.create(nome="SdevBR", slug="sdevbr-e")
        WorkspaceMembership.objects.create(workspace=cls.ws, usuario=cls.user, papel="gestor")
        fontes = [VIRTUOSA_IG, SEU_OCULOS, AGENCIA, MAPS_VIRTUOSA, PARAIBA]
        resultados = [
            {"tipo": "cliente", "classificacao": "Possível cliente", "nome": "oticavirtuosa_", "fonte": VIRTUOSA_IG,
             "servico": "Instagram", "regiao": "", "compatibilidade": 1, "evidencia": "", "texto": ""},
            {"tipo": "cliente", "classificacao": "Possível cliente", "nome": "Ótica Seu Óculos", "fonte": SEU_OCULOS,
             "servico": "Instagram", "regiao": "", "compatibilidade": 1, "evidencia": "", "texto": ""},
            {"tipo": "insuficiente", "classificacao": "Evidência insuficiente", "nome": "Agência", "fonte": AGENCIA,
             "servico": "", "regiao": "", "compatibilidade": 0, "evidencia": "", "texto": ""},
        ]
        cls.pesquisa = PesquisaProspeccao.objects.create(
            workspace=cls.ws, modo="oportunidades", consulta="João Pessoa - PB", chave="teste-empresas",
            criterios={**CRITERIOS, "empresa": "SdevBR"}, fontes=fontes, resultados=resultados,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_tela_esconde_relatorio_unico_e_recolhe_insuficientes(self):
        tela = self.client.get(reverse("prospeccao_pesquisa", args=[self.pesquisa.pk]))
        self.assertNotContains(tela, "Gerar relatório único com todas as evidências")
        self.assertContains(tela, "Outras fontes sem oportunidade (1)")

    @patch("apps.leads.prospeccao.analisar_ia", return_value={"achados": [], "recomendacoes": []})
    def test_relatorio_do_cartao_usa_so_a_mesma_empresa(self, ia):
        self.client.post(reverse("prospeccao_pesquisa", args=[self.pesquisa.pk]), {"relatorio": "0"})
        analise = AnaliseProspeccao.objects.get(pesquisa=self.pesquisa)
        self.assertEqual([x["id"] for x in analise.dados["fontes"]], [1, 5])
        d = analise.dados["diagnostico"]
        instagram = next(c for c in d["canais"] if c["codigo"] == "instagram")
        self.assertEqual([i["valor"] for i in instagram["itens"]], ["@oticavirtuosa_"])
        google = next(c for c in d["canais"] if c["codigo"] == "google")
        self.assertEqual(google["situacao"], "encontrado")
        self.assertNotIn("(83) 99955-8200", d["empresa"]["telefones"])
        self.assertIn("(83) 98887-9222", d["empresa"]["telefones"])
        self.assertEqual(len(ia.call_args.args[2]), 2)
