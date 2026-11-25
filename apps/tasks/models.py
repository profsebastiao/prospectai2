from django.conf import settings
from django.db import models


class Tarefa(models.Model):
    class Status(models.TextChoices):
        FAZER = "fazer", "A fazer"
        ANDAMENTO = "andamento", "Em andamento"
        AGUARDANDO = "aguardando", "Aguardando cliente"
        REVISAO = "revisao", "Em revis\u00e3o"
        CONCLUIDA = "concluida", "Conclu\u00edda"

    class Prioridade(models.TextChoices):
        BAIXA = "baixa", "Baixa"
        MEDIA = "media", "M\u00e9dia"
        ALTA = "alta", "Alta"

    projeto = models.ForeignKey(
        "projects.Projeto",
        on_delete=models.CASCADE,
        related_name="tarefas"
    )
    servico = models.ForeignKey(
        "services.Servico",
        on_delete=models.SET_NULL,
        null=True, blank=True
    )
    modelo_origem = models.ForeignKey(
        "services.ModeloTarefa",
        on_delete=models.SET_NULL,
        null=True, blank=True
    )
    titulo = models.CharField(max_length=200)
    descricao = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.FAZER
    )
    prioridade = models.CharField(
        max_length=10,
        choices=Prioridade.choices,
        default=Prioridade.MEDIA
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True
    )
    prazo = models.DateField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["prazo", "id"]
        verbose_name = "Tarefa"
        verbose_name_plural = "Tarefas"
        constraints = [
            models.UniqueConstraint(
                fields=["projeto", "modelo_origem"],
                name="unique_modelo_tarefa_por_projeto"
            )
        ]

    def __str__(self):
        return self.titulo
