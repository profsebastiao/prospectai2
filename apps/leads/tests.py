from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Workspace, WorkspaceMembership
from apps.leads.busca_web import BuscaIndisponivel, candidatos
from apps.leads.models import Lead


RESULTS = [
    {
        "title": "Café Aurora | Instagram",
        "url": "https://www.instagram.com/cafe.aurora/?igsh=abc",
        "content": (
            "Segmento: Cafeteria; Cidade: Recife; E-mail: contato@cafeaurora.example "
            "Site oficial: https://cafeaurora.example/ "
            "Landing page: https://cafeaurora.example/reservas "
            "https://linktr.ee/cafeaurora https://wa.me/5581999990000"
        ),
    },
    {
        "title": "Café Aurora | TikTok",
        "url": "https://www.tiktok.com/@cafe.aurora",
        "content": "Cidade: Curitiba; Perfil independente.",
    },
    {
        "title": "Café Aurora | Facebook",
        "url": "https://www.facebook.com/cafeaurora",
        "content": "Site oficial: https://cafeaurora.example/",
    },
]


class BuscaLeadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user("busca-demo")
        cls.demo = Workspace.objects.create(nome="Empresa Demo de teste", slug="demo-busca")
        cls.other = Workspace.objects.create(nome="Outra empresa", slug="outra-busca")
        WorkspaceMembership.objects.create(usuario=cls.user, workspace=cls.demo)

    def setUp(self):
        self.client.force_login(self.user)
        self.search_url = reverse("buscar_leads_web")
        self.create_url = reverse("gestao_novo", args=["leads"])

    def test_search_ambiguity_evidence_and_link_classification(self):
        with patch("config.busca_leads_views.buscar", return_value=RESULTS) as provider:
            response = self.client.post(self.search_url, {"consulta": "@cafe.aurora", "cidade": "Recife"})
        self.assertIn("@cafe.aurora", provider.call_args.args[0])
        self.assertContains(response, "Revisar e cadastrar lead", count=3)
        first, second, _ = response.context["resultados"]
        self.assertEqual(first["segmento"], "Cafeteria")
        self.assertEqual(first["cidade"], "Recife")
        self.assertEqual({p["rede"] for p in first["perfis"]}, {"instagram", "facebook"})
        self.assertEqual({p["rede"] for p in second["perfis"]}, {"tiktok"})
        self.assertEqual(
            {x["tipo"] for x in first["links"]},
            {"WhatsApp direto", "Página de links", "Site do negócio", "Landing page"},
        )
        self.assertTrue(first["site_encontrado"])
        self.assertFalse(second["site_encontrado"])
        self.assertContains(response, "Não encontrado nas fontes consultadas")
        self.assertContains(response, "Consultado em")
        self.assertFalse(Lead.objects.exists())

    def test_uncertain_external_page_is_not_inferred_as_official(self):
        result, = candidatos([{"url": "https://aurora.example/catalogo", "title": "Aurora", "content": "Catálogo"}])
        self.assertEqual(result["links"][0]["tipo"], "Revisão manual")

    def test_unavailable_search_prefills_manual_review(self):
        with patch("config.busca_leads_views.buscar", side_effect=BuscaIndisponivel("Busca indisponível.")):
            response = self.client.post(self.search_url, {"consulta": "@Aurora", "rede": "instagram"})
        self.assertContains(response, "Busca indisponível")
        review = self.client.post(self.search_url, {"acao": "revisar", "resultado": response.context["manual"]})
        self.assertEqual(review.context["formulario"].initial["usuario_social"], "aurora")
        self.assertContains(review, f'action="{self.create_url}"')

    def test_review_save_duplicate_and_other_workspace(self):
        Lead.objects.create(
            workspace=self.other, nome="Outra Aurora", rede_social="instagram", usuario_social="cafe.aurora",
        )
        with patch("config.busca_leads_views.buscar", return_value=RESULTS):
            results = self.client.post(self.search_url, {"consulta": "Café Aurora"})
        token = results.context["resultados"][0]["selecao"]
        review = self.client.post(self.search_url, {"acao": "revisar", "resultado": token})
        initial = review.context["formulario"].initial
        self.assertEqual(initial["nome"], "Café Aurora")
        self.assertEqual(initial["perfil_url"], "https://instagram.com/cafe.aurora")
        self.assertEqual(Lead.objects.filter(workspace=self.demo).count(), 0)
        saved = self.client.post(self.create_url, {**initial, "nome": "Nome revisado", "status": "novo"})
        self.assertEqual(saved.status_code, 302)
        lead = Lead.objects.get(workspace=self.demo)
        self.assertEqual(lead.nome, "Nome revisado")
        self.assertEqual(lead.usuario_social, "cafe.aurora")
        duplicate = self.client.post(self.create_url, {
            "nome": "Duplicado", "perfil_url": "https://instagram.com/CAFE.AURORA/?utm_source=foo",
            "status": "novo",
        })
        self.assertContains(duplicate, "Este perfil já está cadastrado nesta empresa")
        self.assertEqual(Lead.objects.filter(workspace=self.demo).count(), 1)

        WorkspaceMembership.objects.create(usuario=self.user, workspace=self.other)
        session = self.client.session
        session["workspace_id"] = self.other.pk
        session.save()
        forbidden = self.client.post(self.search_url, {"acao": "revisar", "resultado": token})
        self.assertEqual(forbidden.status_code, 400)

    def test_legacy_instagram_duplicate(self):
        Lead.objects.create(workspace=self.demo, nome="Legado", instagram="@Casa.Azul")
        response = self.client.post(self.create_url, {
            "nome": "Outra casa", "rede_social": "instagram", "usuario_social": "@CASA.AZUL", "status": "novo",
        })
        self.assertContains(response, "Este perfil já está cadastrado nesta empresa")
