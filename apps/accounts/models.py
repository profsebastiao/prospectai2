from django.conf import settings
from django.db import models
from uuid import uuid4


def avatar_upload_path(instance, filename):
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    if suffix not in {"jpg", "jpeg", "png", "webp"}:
        suffix = "jpg"
    return f"avatars/{uuid4().hex}.{suffix}"


class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="perfil_prospectai",
    )
    foto = models.ImageField(upload_to=avatar_upload_path, blank=True)

    def __str__(self):
        return f"Perfil de {self.usuario}"


class Workspace(models.Model):
    nome = models.CharField(max_length=150)
    slug = models.SlugField(unique=True)
    ramo_prospeccao = models.CharField("ramo de atuação", max_length=150, blank=True)
    publico_prospeccao = models.CharField("perfil de cliente desejado", max_length=250, blank=True)
    regiao_prospeccao = models.CharField("região atendida", max_length=150, blank=True)
    usuarios = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="WorkspaceMembership", blank=True
    )

    class Meta:
        ordering = ["nome", "pk"]
        verbose_name = "Espaço de trabalho"
        verbose_name_plural = "Espaços de trabalho"

    def __str__(self):
        return self.nome


class WorkspaceMembership(models.Model):
    class Papel(models.TextChoices):
        GESTOR = "gestor", "Gestor (acesso total, inclusive financeiro)"
        EQUIPE = "equipe", "Equipe (edita CRM, projetos e tarefas; sem financeiro)"
        LEITURA = "leitura", "Somente leitura (sem financeiro)"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    papel = models.CharField(
        "papel", max_length=10, choices=Papel.choices, default=Papel.EQUIPE,
    )
    pode_gerenciar_gemini = models.BooleanField("pode gerenciar a integracao de IA desta empresa", default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "usuario"], name="accounts_unique_workspace_member"
            )
        ]
        verbose_name = "Vínculo de usuário"
        verbose_name_plural = "Vínculos de usuários"


class WorkspaceOwned(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.PROTECT, editable=False)

    class Meta:
        abstract = True
class IntegracaoGemini(models.Model):
    # "gemini" e "groq" só identificam credenciais antigas, que não são mais usadas.
    PROVEDORES = [
        ("openrouter", "OpenRouter"),
        ("gemini", "Gemini (antigo, não usado)"),
        ("groq", "Groq (antigo, não usado)"),
    ]

    provedor = models.CharField(max_length=20, choices=PROVEDORES, default="openrouter")
    workspace = models.OneToOneField(Workspace, on_delete=models.CASCADE)
    credencial = models.BinaryField(editable=False)
    pesquisa_web = models.BooleanField(default=False)
    modelo_pesquisa = models.CharField(max_length=100, blank=True, editable=False)
    limite_diario = models.PositiveIntegerField()
    ultimo_teste = models.DateTimeField(null=True, blank=True)
    status_teste = models.CharField(max_length=20, blank=True)
    versao = models.PositiveIntegerField(default=1, editable=False)


class EventoIntegracaoGemini(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    evento = models.CharField(max_length=30)
    criado_em = models.DateTimeField(auto_now_add=True)
