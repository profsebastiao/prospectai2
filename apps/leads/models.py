from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.accounts.models import WorkspaceOwned

from .perfis import HOSTS, RedeSocial, domain, handle, instagram_handle, profile_identity, web_url


class Lead(WorkspaceOwned):
    class Status(models.TextChoices):
        NOVO = "novo", "Novo"
        CONTATO = "contato", "Em contato"
        NEGOCIACAO = "negociacao", "Negociação"
        CONVERTIDO = "convertido", "Convertido"
        PERDIDO = "perdido", "Perdido"

    nome = models.CharField(max_length=200)
    empresa = models.CharField(max_length=200, blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    whatsapp = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    instagram = models.CharField(max_length=150, blank=True)
    site = models.URLField(blank=True)
    segmento = models.CharField(max_length=120, blank=True)
    cidade = models.CharField(max_length=120, blank=True)
    rede_social = models.CharField(max_length=20, choices=RedeSocial.choices, blank=True)
    usuario_social = models.CharField("usuário da rede", max_length=150, blank=True)
    perfil_url = models.URLField("URL do perfil", max_length=500, blank=True)
    fonte_prospeccao = models.URLField("fonte da oportunidade", max_length=500, blank=True, editable=False)
    chave_oportunidade = models.CharField(max_length=64, blank=True, editable=False)
    origem = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NOVO
    )
    observacoes = models.TextField(blank=True)
    ultimo_contato = models.DateField("último contato", null=True, blank=True)
    proximo_retorno = models.DateField("próximo retorno", null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Lead"
        verbose_name_plural = "Leads"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "chave_oportunidade"], condition=~Q(chave_oportunidade=""),
                name="lead_oportunidade_workspace_chave",
            ),
            models.UniqueConstraint(
                fields=["workspace", "fonte_prospeccao"],
                condition=~Q(fonte_prospeccao=""),
                name="leads_workspace_unique_prospect_source",
            ),
            models.UniqueConstraint(
                fields=["workspace", "rede_social", "usuario_social"],
                condition=~Q(rede_social="") & ~Q(usuario_social=""),
                name="leads_workspace_unique_social_handle",
            ),
            models.UniqueConstraint(
                fields=["workspace", "perfil_url"],
                condition=~Q(perfil_url=""),
                name="leads_workspace_unique_profile_url",
            ),
        ]

    def clean(self):
        super().clean()
        identity = profile_identity(self.perfil_url)
        if self.perfil_url and not web_url(self.perfil_url):
            raise ValidationError({"perfil_url": "Informe uma URL HTTP ou HTTPS válida."})

        username = handle(self.usuario_social)
        if self.usuario_social and not username:
            raise ValidationError({"usuario_social": "Informe somente o @usuário, sem espaços."})
        if identity:
            rede, perfil_usuario, self.perfil_url = identity
            if self.rede_social and self.rede_social != rede:
                raise ValidationError({"rede_social": "A rede deve corresponder à URL do perfil."})
            if username and perfil_usuario and username != perfil_usuario:
                raise ValidationError({"usuario_social": "O usuário deve corresponder à URL do perfil."})
            self.rede_social = rede
            username = username or perfil_usuario
        elif self.perfil_url:
            self.perfil_url = web_url(self.perfil_url)
            if domain(self.perfil_url) in HOSTS:
                raise ValidationError({"perfil_url": "Informe a URL de um perfil, em vez de uma publicação."})
        if username and not self.rede_social:
            raise ValidationError({"rede_social": "Selecione a rede para identificar o usuário."})
        self.usuario_social = username
        legacy_instagram = instagram_handle(self.instagram)
        if legacy_instagram and not self.rede_social and not self.perfil_url:
            self.rede_social = RedeSocial.INSTAGRAM
            self.usuario_social = legacy_instagram
        if legacy_instagram and self.rede_social == RedeSocial.INSTAGRAM and username and legacy_instagram != username:
            raise ValidationError({"instagram": "O Instagram informado difere do usuário do perfil."})

        if self.workspace_id:
            field = self.duplicate_profile_field()
            if field:
                raise ValidationError({field: "Este perfil já está cadastrado nesta empresa."})

    def duplicate_profile_field(self):
        leads = Lead.objects.filter(workspace_id=self.workspace_id).exclude(pk=self.pk)
        if self.perfil_url and leads.filter(perfil_url=self.perfil_url).exists():
            return "perfil_url"
        if self.rede_social and self.usuario_social and leads.filter(
            rede_social=self.rede_social, usuario_social=self.usuario_social
        ).exists():
            return "usuario_social"

        legacy = instagram_handle(self.instagram)
        primary = self.usuario_social if self.rede_social == RedeSocial.INSTAGRAM else ""
        instagram_ids = {value for value in (legacy, primary) if value}
        if instagram_ids:
            if leads.filter(rede_social=RedeSocial.INSTAGRAM, usuario_social__in=instagram_ids).exists():
                return "instagram" if legacy else "usuario_social"
            existing = leads.exclude(instagram="").values_list("instagram", flat=True)
            if any(instagram_handle(value) in instagram_ids for value in existing):
                return "instagram" if legacy else "usuario_social"
        return ""

    def __str__(self):
        return self.empresa or self.nome


LEAD_STAGES_CLOSED = (Lead.Status.CONVERTIDO, Lead.Status.PERDIDO)


class PesquisaProspeccao(WorkspaceOwned):
    class Modo(models.TextChoices):
        OPORTUNIDADES = "oportunidades", "Oportunidades"
        IDENTIDADE = "identidade", "Análise de lead"

    modo = models.CharField(max_length=20, choices=Modo.choices)
    consulta = models.CharField(max_length=350)
    criterios = models.JSONField(default=dict)
    fontes = models.JSONField(default=list)
    resultados = models.JSONField(default=list)
    avisos = models.JSONField(default=list)
    chave = models.CharField(max_length=64, blank=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "chave"], condition=~Q(chave=""), name="lead_pesquisa_workspace_chave"),
        ]


class AnaliseProspeccao(models.Model):
    pesquisa = models.ForeignKey(PesquisaProspeccao, on_delete=models.CASCADE, related_name="analises")
    candidato = models.PositiveSmallIntegerField()
    nome_lead = models.CharField(max_length=200)
    destinatario = models.CharField(max_length=100, blank=True)
    dados = models.JSONField(default=dict)
    chave = models.CharField(max_length=64, blank=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["pesquisa", "chave"], condition=~Q(chave=""), name="lead_analise_pesquisa_chave"),
        ]


class RelatorioProspeccao(models.Model):
    analise = models.OneToOneField(AnaliseProspeccao, on_delete=models.CASCADE, related_name="relatorio")
    empresa_nome = models.CharField(max_length=150)
    titulo = models.CharField(max_length=200)
    achados = models.TextField()
    recomendacoes = models.TextField(blank=True)
    observacoes_internas = models.TextField(blank=True)
    aprovado_em = models.DateTimeField(null=True, blank=True)
    revisao = models.PositiveIntegerField(default=0)


class LimiteGemini(models.Model):
    dia = models.DateField()
    escopo = models.CharField(max_length=40)
    chamadas = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["dia", "escopo"], name="lead_limite_gemini_dia_escopo"),
        ]
