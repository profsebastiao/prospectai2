from django.apps import apps
from django.db import models, transaction


class Projeto(models.Model):
    class Status(models.TextChoices):
        PLANEJADO = "planejado", "Planejado"
        ANDAMENTO = "andamento", "Em andamento"
        PAUSADO = "pausado", "Pausado"
        CONCLUIDO = "concluido", "Concluído"

    cliente = models.ForeignKey(
        "clients.Cliente",
        on_delete=models.PROTECT,
        related_name="projetos"
    )
    nome = models.CharField(max_length=200)
    descricao = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANEJADO
    )
    data_inicio = models.DateField(null=True, blank=True)
    prazo_entrega = models.DateField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Projeto"
        verbose_name_plural = "Projetos"

    def __str__(self):
        return self.nome


class ProjetoServico(models.Model):
    projeto = models.ForeignKey(
        Projeto,
        on_delete=models.CASCADE,
        related_name="servicos_contratados"
    )
    servico = models.ForeignKey(
        "services.Servico",
        on_delete=models.PROTECT
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["projeto", "servico"],
                name="unique_servico_por_projeto"
            )
        ]
        verbose_name = "Serviço contratado"
        verbose_name_plural = "Serviços contratados"

    def __str__(self):
        return str(self.servico)

    def save(self, *args, **kwargs):
        novo = self._state.adding
        with transaction.atomic():
            super().save(*args, **kwargs)
            if novo:
                ModeloTarefa = apps.get_model(
                    "services", "ModeloTarefa"
                )
                Tarefa = apps.get_model("tasks", "Tarefa")
                modelos = ModeloTarefa.objects.filter(
                    servico_id=self.servico_id
                )
                for modelo in modelos:
                    Tarefa.objects.get_or_create(
                        projeto_id=self.projeto_id,
                        modelo_origem=modelo,
                        defaults={
                            "servico_id": self.servico_id,
                            "titulo": modelo.titulo,
                            "descricao": modelo.descricao,
                        }
                    )
