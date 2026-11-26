from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import IntegracaoGemini, Workspace, WorkspaceMembership
from apps.leads.busca_web import BuscaIndisponivel
from apps.leads.models import AnaliseProspeccao, Lead, PesquisaProspeccao, RelatorioProspeccao
from apps.leads.prospeccao import IAIndisponivel
from apps.services.models import Servico


PEDIDO = "Solicito orçamento para uma cozinha planejada em João Pessoa."
PARCEIRO = "Escritório de arquitetura em João Pessoa com projetos de interiores residenciais."
CONTATO = "Reservas apenas pelo WhatsApp; consulte as opções pelo nosso perfil."
FONTES_MARCENARIA = [
    {"title": "Pedido de cozinha", "url": "https://publicacoes.example/pedido",
     "content": PEDIDO, "published_date": "2026-11-20"},
    {"title": "Escritório Horizonte", "url": "https://arquitetura.example/studio",
     "content": PARCEIRO},
]
FONTES_AGENCIA = [
    {"title": "Café Aurora | Instagram", "url": "https://instagram.com/cafeaurora",
     "content": CONTATO + " Cidade: Recife; WhatsApp: (81) 99999-0101."},
    {"title": "Café Aurora Paraná | TikTok", "url": "https://tiktok.com/@cafeaurora",
     "content": "Cidade: Curitiba; loja de café em grãos."},
]


class ProspeccaoIATests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("assinante-ia")
        cls.outsider = get_user_model().objects.create_user("outra-assinatura")
        cls.marcenaria = Workspace.objects.create(
            nome="Marcenaria Ponto", slug="marcenaria-teste", ramo_prospeccao="Marcenaria",
            publico_prospeccao="Proprietários de imóveis residenciais", regiao_prospeccao="João Pessoa",
        )
        cls.agencia = Workspace.objects.create(
            nome="Agência Horizonte", slug="agencia-teste", ramo_prospeccao="Agência",
            publico_prospeccao="Negócios locais", regiao_prospeccao="Recife",
        )
        for ws in (cls.marcenaria, cls.agencia):
            WorkspaceMembership.objects.create(workspace=ws, usuario=cls.user)
        WorkspaceMembership.objects.create(workspace=cls.agencia, usuario=cls.outsider)
        cls.cozinha = Servico.objects.create(workspace=cls.marcenaria, nome="Cozinhas planejadas")
        cls.landing = Servico.objects.create(workspace=cls.agencia, nome="Landing pages")

    def setUp(self):
        self.client.force_login(self.user)
        self.selecionar(self.marcenaria)

    def evidencias(self, fontes):
        dados = {"evidencias-TOTAL_FORMS": str(len(fontes)), "evidencias-INITIAL_FORMS": "0"}
        for i, fonte in enumerate(fontes):
            for campo in ("title", "url", "content", "published_date"):
                if campo in fonte:
                    dados[f"evidencias-{i}-{campo}"] = fonte[campo]
        return dados

    def selecionar(self, workspace):
        session = self.client.session
        session["workspace_id"] = workspace.pk
        session.save()

    def campos(self, workspace, servico, **extra):
        return {
            "criterios-ramo": workspace.ramo_prospeccao,
            "criterios-publico": workspace.publico_prospeccao,
            "criterios-regiao": workspace.regiao_prospeccao,
            "criterios-servicos": [servico.pk],
            **extra,
        }

    @patch("config.prospeccao_views.analisar_ia")
    def test_marcenaria_demanda_parceiro_revisao_e_duplicidade(self, ia):
        ia.return_value = {"oportunidades": [
            {"tipo": "parceiro", "fonte": 2, "servico": self.cozinha.pk,
             "texto": "Atividade complementar para projetos residenciais.", "evidencia": PARCEIRO},
            {"tipo": "demanda", "fonte": 1, "servico": self.cozinha.pk,
             "texto": "A publicação pede orçamento para o serviço.", "evidencia": PEDIDO},
            {"tipo": "demanda", "fonte": 99, "servico": self.cozinha.pk,
             "texto": "Fonte inexistente.", "evidencia": PEDIDO},
        ]}
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        response = self.client.get(url)
        self.assertContains(response, "João Pessoa")
        self.assertContains(response, "Cozinhas planejadas")
        self.assertNotContains(response, "Landing pages")
        dados = {**self.campos(self.marcenaria, self.cozinha), **self.evidencias(FONTES_MARCENARIA)}
        response = self.client.post(url, dados, follow=True)
        self.assertContains(response, "Demanda explícita")
        self.assertContains(response, "Possível parceiro")
        self.assertContains(response, "2026-11-20")
        results = response.context["pesquisa"].resultados
        self.assertEqual([item["tipo"] for item in results], ["demanda", "parceiro"])
        self.assertEqual(ia.call_args.args[1]["regiao"], "João Pessoa")
        outra = self.client.post(url, dados)
        self.assertEqual(PesquisaProspeccao.objects.count(), 1)
        self.assertIn(str(response.context["pesquisa"].pk), outra["Location"])
        self.assertFalse(Lead.objects.exists())
        path = reverse("prospeccao_pesquisa", args=[response.context["pesquisa"].pk])
        self.client.post(path, {"selecionados": ["0", "1"]})
        self.client.post(path, {"selecionados": ["0", "1"]})
        self.assertEqual(Lead.objects.filter(workspace=self.marcenaria).count(), 2)
        self.assertFalse(Lead.objects.filter(workspace=self.agencia).exists())

    @patch("config.prospeccao_views.sugerir_consultas", return_value=["Landing pages para negócios locais em Olinda"])
    def test_criterios_somente_mudam_ao_salvar_e_usam_catalogo_da_empresa(self, sugerir):
        self.selecionar(self.agencia)
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        dados = self.campos(self.agencia, self.landing, **{"criterios-regiao": "Olinda", "acao": "sugerir"})
        response = self.client.post(url, dados)
        self.assertContains(response, "Landing pages para negócios locais em Olinda")
        self.agencia.refresh_from_db()
        self.assertEqual(self.agencia.regiao_prospeccao, "Recife")
        criterios = sugerir.call_args.args[0]
        self.assertEqual(criterios["publico"], "Negócios locais")
        self.assertEqual(criterios["servicos"], [{"id": self.landing.pk, "nome": "Landing pages"}])
        response = self.client.post(url, {**dados, "criterios-salvar": "on"})
        self.assertTrue(response.context["criterios"].errors)
        self.agencia.refresh_from_db()
        self.assertEqual(self.agencia.regiao_prospeccao, "Recife")
        self.user.user_permissions.add(Permission.objects.get(codename="change_workspace"))
        self.client.post(url, {**dados, "criterios-salvar": "on"})
        self.agencia.refresh_from_db()
        self.assertEqual(self.agencia.regiao_prospeccao, "Olinda")
        response = self.client.post(url, {**dados, "criterios-servicos": [self.cozinha.pk]})
        self.assertTrue(response.context["criterios"].errors)
        self.assertFalse(PesquisaProspeccao.objects.exists())

    @patch("apps.leads.prospeccao.analisar_ia")
    def test_agencia_confirma_perfil_relatorio_aprovacao_exportacao_e_isolamento(self, ia):
        self.selecionar(self.agencia)
        ia.return_value = {
            "achados": [{"fonte": 1, "texto": "Reservas encaminhadas pelo WhatsApp.", "evidencia": CONTATO}],
            "recomendacoes": [
                {"fonte": 1, "servico": self.landing.pk,
                 "texto": "Uma landing page pode organizar opções e facilitar reservas.",
                 "evidencia": CONTATO},
                {"fonte": 2, "servico": self.landing.pk,
                 "texto": "Recomendação errada.", "evidencia": "loja de café em grãos"},
            ],
        }
        url = reverse("prospeccao_iniciar", args=["identidade"])
        response = self.client.post(url, {
            **self.campos(self.agencia, self.landing), "consulta": "@cafeaurora",
            "rede": "instagram", "cidade": "Recife", "destinatario": "Pessoa proprietária",
            **self.evidencias(FONTES_AGENCIA),
        }, follow=True)
        self.assertContains(response, "Confirmar este candidato", count=2)
        pesquisa = response.context["pesquisa"]
        self.assertEqual(pesquisa.consulta, "@cafeaurora")
        self.assertFalse(ia.called)
        path = reverse("prospeccao_pesquisa", args=[pesquisa.pk])
        response = self.client.post(path, {"candidato": "0"}, follow=True)
        registro = response.context["analise"]
        self.assertContains(response, "Landing pages")
        self.assertContains(response, CONTATO)
        self.assertContains(response, "Não localizado no material fornecido")
        self.assertNotContains(response, "Recomendação errada")
        self.assertEqual([f["id"] for f in ia.call_args.args[2]], [1])

        path_analise = reverse("prospeccao_analise", args=[registro.pk])
        response = self.client.post(path_analise, follow=True)
        rel = response.context["relatorio"]
        edit = reverse("prospeccao_relatorio", args=[rel.pk])
        export = reverse("prospeccao_exportar", args=[rel.pk])
        self.assertEqual(self.client.get(export).status_code, 403)
        dados = {
            "titulo": rel.titulo, "achados": rel.achados, "recomendacoes": rel.recomendacoes,
            "observacoes_internas": "SEGREDO INTERNO", "revisao": 0, "acao": "aprovar",
        }
        self.assertEqual(self.client.post(edit, dados).status_code, 302)
        download = self.client.get(export)
        self.assertContains(download, "https://instagram.com/cafeaurora")
        self.assertContains(download, "Pessoa proprietária")
        self.assertNotContains(download, "SEGREDO INTERNO")
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertEqual(self.client.post(edit, dados).status_code, 409)
        self.assertEqual(self.client.post(edit, {**dados, "revisao": 1, "acao": "rascunho"}).status_code, 302)
        self.assertEqual(self.client.get(export).status_code, 403)
        self.assertEqual(self.client.post(edit, {**dados, "revisao": 2}).status_code, 302)

        self.selecionar(self.marcenaria)
        for denied in (path, path_analise, edit, export):
            self.assertEqual(self.client.get(denied).status_code, 404)
            self.assertEqual(self.client.post(denied).status_code, 405 if denied == export else 404)
        self.assertNotContains(self.client.get(reverse("prospeccao_historico")), "@cafeaurora")
        self.selecionar(self.agencia)
        self.assertEqual(self.client.get(export).status_code, 200)

    @patch("apps.leads.prospeccao.buscar", side_effect=BuscaIndisponivel("Busca não configurada."))
    def test_falha_busca_e_fluxos_existentes(self, busca):
        IntegracaoGemini.objects.create(
            workspace=self.marcenaria, credencial=b"nao-utilizada", limite_diario=10, provedor="openrouter",
            pesquisa_web=True, modelo_pesquisa="openrouter/free",
        )
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        response = self.client.post(url, self.campos(self.marcenaria, self.cozinha, acao="web"), follow=True)
        self.assertContains(response, "Busca não configurada")
        self.assertFalse(Lead.objects.exists())
        for name, args in (
            ("gestao_lista", ["leads"]), ("gestao_novo", ["leads"]),
            ("buscar_leads_web", []), ("conversao_leads", []),
        ):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)
        other = Lead.objects.create(workspace=self.agencia, nome="Lead privado")
        self.assertEqual(self.client.get(reverse("prospeccao_iniciar", args=["identidade"]), {"lead": other.pk}).status_code, 404)

    @patch("apps.leads.prospeccao.analisar_ia", side_effect=IAIndisponivel("IA sem configuração."))
    def test_falha_ia_mantem_canais_coletados(self, ia):
        self.selecionar(self.agencia)
        url = reverse("prospeccao_iniciar", args=["identidade"])
        response = self.client.post(url, {**self.campos(self.agencia, self.landing), "consulta": "@cafeaurora", **self.evidencias(FONTES_AGENCIA)}, follow=True)
        path = reverse("prospeccao_pesquisa", args=[response.context["pesquisa"].pk])
        response = self.client.post(path, {"candidato": "0"}, follow=True)
        self.assertContains(response, "IA sem configuração")
        self.assertContains(response, "https://instagram.com/cafeaurora")
        self.assertFalse(response.context["analise"].dados["recomendacoes"])

    @patch("apps.leads.prospeccao.buscar")
    @patch("apps.leads.prospeccao.analisar_ia")
    def test_piloto_exige_texto_e_nao_abre_link(self, ia, busca):
        self.selecionar(self.agencia)
        url = reverse("prospeccao_iniciar", args=["identidade"])
        dados = {**self.campos(self.agencia, self.landing), "consulta": "@cafeaurora"}
        response = self.client.post(url, {**dados, **self.evidencias([])})
        self.assertContains(response, "Cole pelo menos um trecho público")
        response = self.client.post(url, {**dados, **self.evidencias([{"url": "https://instagram.com/cafeaurora"}])})
        self.assertContains(response, "Um link sozinho não foi consultado.")
        response = self.client.post(url, {**dados, "acao": "web"})
        self.assertContains(response, "Conecte o OpenRouter")
        busca.assert_not_called()
        ia.assert_not_called()
        self.assertFalse(PesquisaProspeccao.objects.exists())

    @patch("config.prospeccao_views.analisar_ia", side_effect=IAIndisponivel("Cota esgotada."))
    def test_retry_preserva_trecho_sem_url_e_evita_pesquisa_duplicada(self, ia):
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        fontes = [{"title": "Pedido copiado", "content": PEDIDO}]
        dados = {**self.campos(self.marcenaria, self.cozinha), **self.evidencias(fontes)}
        r = self.client.post(url, dados, follow=True)
        self.assertContains(r, PEDIDO)
        self.assertContains(r, "Cota esgotada")
        registro = r.context["pesquisa"]
        r = self.client.get(url, {"repetir": registro.pk})
        self.assertContains(r, "Solicito orçamento")
        ia.side_effect = None
        ia.return_value = {"oportunidades": [{
            "fonte": 1, "tipo": "demanda", "servico": self.cozinha.pk,
            "texto": "O pedido cita a oferta.", "evidencia": PEDIDO,
        }]}
        r = self.client.post(url, dados, follow=True)
        self.assertContains(r, "Demanda explícita")
        self.assertEqual(PesquisaProspeccao.objects.count(), 1)
        path = reverse("prospeccao_pesquisa", args=[registro.pk])
        self.client.post(path, {"selecionados": ["0"]})
        self.client.post(path, {"selecionados": ["0"]})
        self.assertEqual(Lead.objects.count(), 1)

    @patch("config.prospeccao_views.sugerir_consultas", return_value=["Clínicas sem site em João Pessoa"])
    def test_pagina_guia_pesquisa_e_aceita_cinco_evidencias(self, sugerir):
        self.selecionar(self.agencia)
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        pagina = self.client.get(url)
        self.assertContains(pagina, "Descreva sua empresa")
        self.assertContains(pagina, "Ex.: João Pessoa - PB")
        self.assertEqual(pagina.context["evidencias"].total_form_count(), 5)
        resposta = self.client.post(url, self.campos(self.agencia, self.landing, acao="sugerir"))
        self.assertContains(resposta, "https://www.google.com/search?q=Cl%C3%ADnicas%20sem%20site%20em%20Jo%C3%A3o%20Pessoa")
        self.assertContains(resposta, 'data-copiar="Clínicas sem site em João Pessoa"')

    @patch("config.prospeccao_views.sugerir_consultas")
    def test_criterios_recusam_perfil_ou_link(self, sugerir):
        self.selecionar(self.agencia)
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        for campo, valor in (
            ("criterios-ramo", "m.fatimatavares"),
            ("criterios-publico", "@ m.fatimatavares"),
            ("criterios-regiao", "https://instagram.com/x"),
        ):
            resposta = self.client.post(url, self.campos(self.agencia, self.landing, acao="sugerir", **{campo: valor}))
            self.assertContains(resposta, "parece um @perfil ou um link")
        sugerir.assert_not_called()
        resposta = self.client.post(url, self.campos(
            self.agencia, self.landing, acao="sugerir",
            **{"criterios-ramo": "Agência de marketing digital", "criterios-regiao": "João Pessoa - PB"},
        ))
        self.assertNotContains(resposta, "parece um @perfil ou um link")

    @patch("config.prospeccao_views.analisar_ia")
    def test_evidencia_so_com_nome_nao_gasta_chamada(self, ia):
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        for trecho in ("equipadoraevolusom", "@equipadoraevolusom", "https://instagram.com/equipadoraevolusom"):
            resposta = self.client.post(url, {
                **self.campos(self.marcenaria, self.cozinha, acao="manual"),
                **self.evidencias([{"content": trecho}]),
            })
            self.assertContains(resposta, "parece só um nome")
        ia.assert_not_called()
        self.assertContains(resposta, "data-aguarde=")

    @patch("apps.leads.prospeccao.gerar")
    def test_consultas_sugeridas_ficam_prontas_para_o_google(self, gerar):
        from apps.leads.prospeccao import sugerir_consultas
        gerar.return_value = {"consultas": [
            "1. som automotivo João Pessoa instagram",
            '"indicação site oficina João Pessoa"',
            "Lojas de som que usam o Google Meu Negócio (ID 7) em João Pessoa",
            "som automotivo João Pessoa instagram",
        ]}
        criterios = {"ramo": "Agência", "publico": "Lojas de som", "regiao": "João Pessoa",
                     "servicos": [{"id": 7, "nome": "Google Meu Negócio"}]}
        self.assertEqual(sugerir_consultas(criterios, self.agencia.pk), [
            "som automotivo João Pessoa instagram",
            "indicação site oficina João Pessoa",
            "Lojas de som que usam o Google Meu Negócio em João Pessoa",
        ])
        instrucao = gerar.call_args.args[1]
        self.assertIn("CLIENTES", instrucao)
        self.assertIn("nunca agências", instrucao)

    @patch("config.prospeccao_views.analisar_ia", return_value={"oportunidades": []})
    def test_lista_de_links_do_linktree_e_aceita(self, ia):
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        links = (
            "https://www.google.com/maps/place/Evolusom/@-7.1,-34.8,17z\n"
            "https://api.whatsapp.com/send/?phone=5583996112970&text&type=phone_number\n"
            "https://www.instagram.com/equipadoraevolusom/"
        )
        resposta = self.client.post(url, {
            **self.campos(self.marcenaria, self.cozinha, acao="manual"),
            **self.evidencias([{"content": links}]),
        })
        self.assertNotContains(resposta, "parece só um nome", status_code=302)
        self.assertEqual(resposta.status_code, 302)

    @patch("config.prospeccao_views.analisar_ia")
    def test_aviso_aponta_qual_evidencia_corrigir(self, ia):
        url = reverse("prospeccao_iniciar", args=["oportunidades"])
        resposta = self.client.post(url, {
            **self.campos(self.marcenaria, self.cozinha, acao="manual"),
            **self.evidencias([{"content": PEDIDO}, {"content": "Loja de móveis planejados em João Pessoa com showroom."},
                               {"content": "evolusom"}]),
        })
        self.assertContains(resposta, "Corrija a evidência 3")
        ia.assert_not_called()
