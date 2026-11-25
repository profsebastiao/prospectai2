from django.core.exceptions import ValidationError
from django.db import models

from apps.accounts.models import WorkspaceOwned

class Servico(WorkspaceOwned):
    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True)

    def clean(self):
        super().clean()
        if self.workspace_id and Servico.objects.filter(
            workspace_id=self.workspace_id, nome=self.nome
        ).exclude(pk=self.pk).exists():
            raise ValidationError({"nome": "Este nome de servico ja existe neste espaco."})

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "nome"], name="services_unique_workspace_name"
            )
        ]
        ordering = ["nome"]
        verbose_name = "Serviço"
        verbose_name_plural = "Serviços"

    def __str__(self):
        return self.nome


class ModeloTarefa(models.Model):
    servico = models.ForeignKey(
        Servico, on_delete=models.CASCADE,
        related_name="modelos_tarefas"
    )
    titulo = models.CharField(max_length=200)
    descricao = models.TextField(blank=True)
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["ordem", "id"]
        verbose_name = "Modelo de tarefa"
        verbose_name_plural = "Modelos de tarefas"

    def __str__(self):
        return self.titulo
