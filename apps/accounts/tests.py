from datetime import date
from decimal import Decimal
from io import BytesIO
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.clients.models import Cliente
from apps.finance.models import Cobranca, Contrato, Pagamento
from apps.leads.models import Lead
from apps.projects.models import Projeto, ProjetoServico
from apps.services.models import ModeloTarefa, Servico
from apps.tasks.models import Tarefa

from .models import PerfilUsuario, Workspace, WorkspaceMembership


class WorkspaceAccessTests(TestCase):
    password = "test-workspace-secret"

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser("platform", password=cls.password)
        cls.a = cls.make_company("Empresa-Alfa", "alfa")
        cls.b = cls.make_company("Empresa-Beta", "beta")
        # Staff status by itself must not grant platform privileges.
        cls.b["user"].is_staff = True
        cls.b["user"].save(update_fields=["is_staff"])

    @classmethod
    def make_company(cls, name, slug):
        user = get_user_model().objects.create_user(slug, password=cls.password)
        workspace = Workspace.objects.create(nome=name, slug=slug)
        WorkspaceMembership.objects.create(workspace=workspace, usuario=user)
        lead = Lead.objects.create(workspace=workspace, nome=f"Lead-{name}")
        customer = Cliente.objects.create(workspace=workspace, nome=f"Cliente-{name}")
        service = Servico.objects.create(workspace=workspace, nome=f"Servico-{name}")
        template = ModeloTarefa.objects.create(servico=service, titulo=f"Modelo-{name}")
        project = Projeto.objects.create(cliente=customer, nome=f"Projeto-{name}")
        ProjetoServico.objects.create(projeto=project, servico=service)
        task = Tarefa.objects.get(projeto=project, modelo_origem=template)
        task.prazo = timezone.localdate()
        task.save(update_fields=["prazo"])
        today = date.today()
        contract = Contrato.objects.create(
            cliente=customer, projeto=project, descricao=f"Contrato-{name}",
            valor=Decimal("100"), inicio=today.replace(day=1),
        )
        return dict(user=user, workspace=workspace, lead=lead, customer=customer,
                    service=service, template=template, project=project, task=task,
                    contract=contract)

    def sign_in(self, user):
        self.assertRedirects(
            self.client.post(reverse("prospectai_login"), {
                "username": user.username, "password": self.password,
            }),
            reverse("dashboard"), fetch_redirect_response=False,
        )

    def test_login_and_lists_are_isolated_in_both_directions(self):
        self.assertRedirects(
            self.client.get(reverse("dashboard")), reverse("prospectai_login") + "?next=/"
        )
        for own, other in [(self.a, self.b), (self.b, self.a)]:
            self.client.post(reverse("prospectai_logout"))
            with self.subTest(user=own["user"].username):
                self.sign_in(own["user"])
                self.assertEqual(int(self.client.session["_auth_user_id"]), own["user"].pk)
                response = self.client.get(reverse("dashboard"))
                self.assertEqual(response.context["total_leads"], 1)
                self.assertEqual(response.context["total_clientes"], 1)
                for url, key, attr in [
                    (reverse("portal_clientes"), "customer", "nome"),
                    (reverse("gestao_lista", args=["leads"]), "lead", "nome"),
                    (reverse("gestao_lista", args=["servicos"]), "service", "nome"),
                    (reverse("gestao_lista", args=["projetos"]), "project", "nome"),
                    (reverse("gestao_lista", args=["tarefas"]), "task", "titulo"),
                    (reverse("agenda"), "task", "titulo"),
                    (reverse("kanban"), "task", "titulo"),
                    (reverse("gestao_lista", args=["contratos"]), "contract", "descricao"),
                ]:
                    response = self.client.get(url)
                    with self.subTest(url=url):
                        self.assertContains(response, getattr(own[key], attr))
                        self.assertNotContains(response, getattr(other[key], attr))
                self.assertEqual(self.client.get(reverse("financeiro")).status_code, 200)

    def test_direct_urls_and_posts_require_record_in_workspace(self):
        self.sign_in(self.a["user"])
        for route, key in [
            ("cliente_detalhe", "customer"), ("portal_cliente_editar", "customer"),
            ("projeto_detalhe", "project"),
        ]:
            self.assertEqual(self.client.get(reverse(route, args=[self.a[key].pk])).status_code, 200)
            foreign_url = reverse(route, args=[self.b[key].pk])
            self.assertEqual(self.client.get(foreign_url).status_code, 404)
            self.assertEqual(self.client.post(foreign_url, {"nome": "Alterado"}).status_code, 404)
        for section, key in [
            ("leads", "lead"), ("servicos", "service"), ("projetos", "project"),
            ("tarefas", "task"), ("contratos", "contract"),
        ]:
            url = reverse("gestao_editar", args=[section, self.b[key].pk])
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, {"nome": "Alterado"}).status_code, 404)
        for task, code in [(self.b["task"], 404), (self.a["task"], 302)]:
            self.assertEqual(self.client.post(reverse("kanban"), {
                "tarefa_id": task.pk, "status": Tarefa.Status.CONCLUIDA,
            }).status_code, code)
        self.a["task"].refresh_from_db()
        self.b["task"].refresh_from_db()
        self.assertEqual(self.a["task"].status, Tarefa.Status.CONCLUIDA)
        self.assertEqual(self.b["task"].status, Tarefa.Status.FAZER)

    def test_relationship_fields_and_conversion_are_scoped(self):
        self.sign_in(self.a["user"])
        response = self.client.post(reverse("projeto_contratacao_novo"), {
            "cliente": self.b["customer"].pk, "servicos": [self.b["service"].pk],
            "nome": "Projeto indevido", "status": Projeto.Status.PLANEJADO,
        })
        self.assertEqual(set(response.context["form"].errors), {"cliente", "servicos"})
        self.assertFalse(Projeto.objects.filter(nome="Projeto indevido").exists())
        response = self.client.post(reverse("gestao_novo", args=["tarefas"]), {
            "projeto": self.a["project"].pk, "servico": self.b["service"].pk,
            "modelo_origem": self.b["template"].pk, "responsavel": self.b["user"].pk,
            "titulo": "Tarefa indevida", "status": Tarefa.Status.FAZER,
            "prioridade": Tarefa.Prioridade.MEDIA,
        })
        self.assertEqual(set(response.context["formulario"].errors), {
            "servico", "modelo_origem", "responsavel",
        })
        self.assertFalse(Tarefa.objects.filter(titulo="Tarefa indevida").exists())
        url = reverse("conversao_leads")
        self.assertEqual(self.client.post(url, {"lead_id": self.b["lead"].pk}).status_code, 404)
        self.assertEqual(self.client.post(url, {"lead_id": self.a["lead"].pk}).status_code, 302)
        self.assertEqual(Cliente.objects.get(lead_origem=self.a["lead"]).workspace, self.a["workspace"])

    def test_new_customer_has_no_login_and_uses_active_workspace(self):
        self.sign_in(self.a["user"])
        users = get_user_model().objects.count()
        url = reverse("portal_cliente_novo")
        self.assertEqual(self.client.post(url, {
            "nome": "Ficha comercial", "email": "commercial@example.com", "ativo": "on",
            "workspace": self.b["workspace"].pk,
        }).status_code, 302)
        record = Cliente.objects.get(nome="Ficha comercial")
        self.assertEqual(record.workspace, self.a["workspace"])
        self.assertEqual(get_user_model().objects.count(), users)
        self.assertEqual(self.client.post(reverse("portal_cliente_editar", args=[record.pk]), {
            "nome": "Ficha atualizada", "ativo": "on",
        }).status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.nome, "Ficha atualizada")

    def test_financial_generation_and_related_forms_stay_in_workspace(self):
        self.sign_in(self.a["user"])
        today = date.today()
        period = today.strftime("%Y-%m")
        url = reverse("gerar_mensalidades_pagina")
        data = {"mes": period, "meses": "1", "acao": "gerar", "confirmar": "sim"}
        response = self.client.post(url, {**data, "contrato": self.b["contract"].pk})
        self.assertContains(response, "Contrato mensal ativo nao encontrado")
        self.assertFalse(Cobranca.objects.exists())
        self.assertContains(self.client.post(url, data), "CRIADA")
        self.assertEqual(Cobranca.objects.get().contrato, self.a["contract"])
        self.client.post(url, data)
        self.assertEqual(Cobranca.objects.count(), 1)

        foreign_charge = Cobranca.objects.create(
            contrato=self.b["contract"], competencia=today.replace(day=1),
            vencimento=today, valor=100,
        )
        foreign_payment = Pagamento.objects.create(cobranca=foreign_charge, data=today, valor=50)
        for section, record in [("cobrancas", foreign_charge), ("pagamentos", foreign_payment)]:
            self.assertEqual(self.client.get(reverse("gestao_editar", args=[section, record.pk])).status_code, 404)
        response = self.client.post(reverse("gestao_novo", args=["pagamentos"]), {
            "cobranca": foreign_charge.pk, "data": today.isoformat(), "valor": "1", "forma": "pix",
        })
        self.assertIn("cobranca", response.context["formulario"].errors)
        self.assertEqual(Pagamento.objects.count(), 1)

    def test_platform_admin_switching_and_membership_revocation(self):
        self.sign_in(self.b["user"])
        switch = reverse("workspace_select")
        admin_index = reverse("admin:index")
        self.assertEqual(self.client.get(admin_index).status_code, 403)
        self.assertEqual(self.client.get(reverse("portal_administracao")).status_code, 403)
        self.assertNotContains(self.client.get(switch), self.a["workspace"].nome)
        self.assertEqual(self.client.post(switch, {"workspace": self.a["workspace"].pk}).status_code, 404)
        self.assertEqual(self.client.post(switch, {"workspace": self.b["workspace"].pk}).status_code, 302)
        membership = WorkspaceMembership.objects.get(usuario=self.b["user"])
        membership.delete()
        self.assertRedirects(self.client.get(reverse("dashboard")), switch)
        self.client.post(reverse("prospectai_logout"))

        self.sign_in(self.admin)
        self.assertEqual(self.client.get(admin_index).status_code, 200)
        self.assertContains(self.client.get(reverse("admin:accounts_workspace_changelist")), "Empresa-Beta")
        self.client.post(switch, {"workspace": self.a["workspace"].pk})
        customers = self.client.get(reverse("admin:clients_cliente_changelist"))
        self.assertContains(customers, self.a["customer"].nome)
        self.assertNotContains(customers, self.b["customer"].nome)
        self.client.post(switch, {"workspace": self.b["workspace"].pk})
        self.assertContains(self.client.get(reverse("portal_clientes")), self.b["customer"].nome)

    def test_user_with_two_memberships_can_choose_only_an_authorized_workspace(self):
        WorkspaceMembership.objects.create(workspace=self.b["workspace"], usuario=self.a["user"])
        self.sign_in(self.a["user"])
        switch = reverse("workspace_select")
        self.assertRedirects(self.client.get(reverse("dashboard")), switch)
        for own, other in [(self.a, self.b), (self.b, self.a)]:
            self.client.post(switch, {"workspace": own["workspace"].pk})
            response = self.client.get(reverse("portal_clientes"))
            self.assertContains(response, own["customer"].nome)
            self.assertNotContains(response, other["customer"].nome)
            profile = self.client.get(reverse("dashboard"))
            self.assertContains(profile, own["workspace"].nome)
            self.assertNotContains(profile, other["workspace"].nome)
            self.assertContains(profile, self.a["user"].username)
            self.assertContains(profile, reverse("configuracoes"))
            self.assertContains(profile, switch)


class UserProfilePhotoTests(TestCase):
    def setUp(self):
        self.storage_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.storage_dir.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.storage_dir.cleanup)
        self.user = get_user_model().objects.create_user(
            "profile-photo", password="test-profile-secret", first_name="Ana",
            last_name="Silva", email="ana@example.test",
        )
        self.company = Workspace.objects.create(nome="Empresa Perfil", slug="empresa-perfil")
        WorkspaceMembership.objects.create(workspace=self.company, usuario=self.user)
        self.client.force_login(self.user)

    @staticmethod
    def png_upload(name="avatar.png"):
        buffer = BytesIO()
        Image.new("RGB", (12, 12), (30, 170, 100)).save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def test_sidebar_uses_authenticated_profile_and_initials_without_photo(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Ana Silva")
        self.assertContains(response, "ana@example.test")
        self.assertContains(response, "Empresa Perfil")
        self.assertContains(response, "Trocar empresa")
        self.assertContains(response, reverse("configuracoes"))
        self.assertContains(response, "AS")
        self.assertNotContains(response, "/media/avatars/")
        self.assertEqual(self.client.get(reverse("configuracoes")).status_code, 200)
        self.assertEqual(self.client.get(reverse("workspace_select")).status_code, 200)
        self.assertEqual(self.client.get(reverse("foto_perfil_usuario")).status_code, 404)

    def test_user_can_upload_and_remove_profile_photo(self):
        response = self.client.post(reverse("configuracoes"), {
            "acao": "foto_perfil", "foto": self.png_upload(),
        })
        self.assertRedirects(response, reverse("configuracoes"))
        profile = PerfilUsuario.objects.get(usuario=self.user)
        self.assertTrue(profile.foto)
        self.assertTrue(profile.foto.storage.exists(profile.foto.name))
        image_response = self.client.get(reverse("foto_perfil_usuario"))
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response["Content-Type"], "image/png")
        self.assertEqual(image_response["Cache-Control"], "private, max-age=300")
        self.assertIn(b"PNG", b"".join(image_response.streaming_content))
        image_response.close()

        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, reverse("foto_perfil_usuario"))
        self.assertContains(self.client.get(reverse("configuracoes")), "Remover foto atual")
        arquivo_antigo = profile.foto.name

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("configuracoes"), {
                "acao": "foto_perfil", "remover_foto": "on",
            })
        self.assertRedirects(response, reverse("configuracoes"))
        profile.refresh_from_db()
        self.assertFalse(profile.foto)
        self.assertFalse(profile.foto.storage.exists(arquivo_antigo))
        self.assertContains(self.client.get(reverse("dashboard")), "AS")

        self.client.logout()
        self.assertRedirects(
            self.client.get(reverse("foto_perfil_usuario")),
            reverse("prospectai_login") + "?next=/conta/foto/",
        )

    def test_invalid_image_is_rejected_without_creating_profile(self):
        response = self.client.post(reverse("configuracoes"), {
            "acao": "foto_perfil",
            "foto": SimpleUploadedFile("avatar.png", b"isso nao e uma imagem", content_type="image/png"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["foto_perfil"].errors)
        self.assertFalse(PerfilUsuario.objects.filter(usuario=self.user).exists())

    def test_avatar_is_available_before_a_multi_company_user_selects_a_workspace(self):
        response = self.client.post(reverse("configuracoes"), {
            "acao": "foto_perfil", "foto": self.png_upload(),
        })
        self.assertRedirects(response, reverse("configuracoes"))
        another_company = Workspace.objects.create(nome="Outra Empresa", slug="outra-empresa")
        WorkspaceMembership.objects.create(workspace=another_company, usuario=self.user)
        session = self.client.session
        session.pop("workspace_id", None)
        session.save()

        image_response = self.client.get(reverse("foto_perfil_usuario"))
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response["Content-Type"], "image/png")
        image_response.close()

    def test_long_names_and_email_wrap_in_sidebar_and_missing_values_fall_back(self):
        self.user.first_name = "Nome" * 15
        self.user.last_name = "Sobrenome" * 8
        self.user.email = "endereco.muito.longo." * 5 + "@example.test"
        self.user.save()
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, self.user.get_full_name())
        self.assertContains(response, self.user.email)

        self.user.first_name = ""
        self.user.last_name = ""
        self.user.email = ""
        self.user.save()
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, self.user.username)
        self.assertNotContains(response, "sidebar-profile-email")
