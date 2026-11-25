from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction


class Contrato(models.Model):
    class Periodicidade(models.TextChoices):
        UNICO = "unico", "Pagamento unico"
        MENSAL = "mensal", "Mensal"

    cliente = models.ForeignKey(
        "clients.Cliente",
        on_delete=models.PROTECT,
        related_name="contratos",
    )
    projeto = models.ForeignKey(
        "projects.Projeto",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contratos",
    )
    descricao = models.CharField(max_length=200)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    periodicidade = models.CharField(
        max_length=12,
        choices=Periodicidade.choices,
        default=Periodicidade.MENSAL,
    )
    inicio = models.DateField()
    termino = models.DateField(null=True, blank=True)
    dia_vencimento = models.PositiveSmallIntegerField(
        default=10,
        help_text="Dia de 1 a 31. Ajustar ao ultimo dia do mes quando necessario.",
    )
    ativo = models.BooleanField(default=True)
    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()

        if self.valor is not None and self.valor < 0:
            raise ValidationError({"valor": "Valor nao pode ser negativo."})

        if not 1 <= self.dia_vencimento <= 31:
            raise ValidationError({
                "dia_vencimento": "Informe um dia entre 1 e 31."
            })

        if self.termino and self.inicio and self.termino < self.inicio:
            raise ValidationError({
                "termino": "Termino anterior ao inicio."
            })

        if (
            self.projeto_id
            and self.cliente_id
            and self.projeto.cliente_id != self.cliente_id
        ):
            raise ValidationError({
                "projeto": "O projeto deve pertencer ao cliente do contrato."
            })

    def __str__(self):
        return f"{self.cliente} - {self.descricao}"


class Cobranca(models.Model):
    class Situacao(models.TextChoices):
        ABERTA = "aberta", "Aberta"
        CANCELADA = "cancelada", "Cancelada"

    contrato = models.ForeignKey(
        Contrato,
        on_delete=models.PROTECT,
        related_name="cobrancas",
    )
    competencia = models.DateField(
        help_text="Utilize o primeiro dia do mes de referencia."
    )
    vencimento = models.DateField()
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    situacao = models.CharField(
        max_length=12,
        choices=Situacao.choices,
        default=Situacao.ABERTA,
    )
    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["contrato", "competencia"],
                name="finance_contrato_competencia_unica",
            )
        ]

    def clean(self):
        super().clean()
        if self.valor is not None and self.valor < 0:
            raise ValidationError({"valor": "Valor nao pode ser negativo."})
        if self.competencia and self.competencia.day != 1:
            raise ValidationError({
                "competencia": "Use o primeiro dia do mes."
            })

    @property
    def total_pago(self):
        return sum(
            (p.valor for p in self.pagamentos.all()),
            Decimal("0.00"),
        )

    @property
    def saldo(self):
        return self.valor - self.total_pago

    def __str__(self):
        return f"{self.contrato} - {self.competencia:%m/%Y}"


class Pagamento(models.Model):
    class Forma(models.TextChoices):
        PIX = "pix", "Pix"
        TRANSFERENCIA = "transferencia", "Transferencia"
        DINHEIRO = "dinheiro", "Dinheiro"
        CARTAO = "cartao", "Cartao"
        OUTRO = "outro", "Outro"

    cobranca = models.ForeignKey(
        Cobranca,
        on_delete=models.PROTECT,
        related_name="pagamentos",
    )
    data = models.DateField()
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    forma = models.CharField(
        max_length=20,
        choices=Forma.choices,
        default=Forma.PIX,
    )
    referencia = models.CharField(max_length=120, blank=True)
    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()

        if self.valor is not None and self.valor <= 0:
            raise ValidationError({"valor": "Informe um pagamento positivo."})

        if self.cobranca_id and self.valor is not None:
            outros = self.cobranca.pagamentos.exclude(pk=self.pk)
            total = sum(
                (p.valor for p in outros),
                Decimal("0.00"),
            )
            if total + self.valor > self.cobranca.valor:
                raise ValidationError({
                    "valor": "Pagamento ultrapassa o valor da cobranca."
                })

            if self.cobranca.situacao == Cobranca.Situacao.CANCELADA:
                raise ValidationError({
                    "cobranca": "Nao registre pagamento em cobranca cancelada."
                })

    def save(self, *args, **kwargs):
        # Trava a cobranca e confere o saldo de novo no momento de gravar,
        # para dois pagamentos simultaneos nao ultrapassarem o valor devido.
        with transaction.atomic():
            cobranca = Cobranca.objects.select_for_update().get(pk=self.cobranca_id)
            if cobranca.situacao == Cobranca.Situacao.CANCELADA:
                raise ValidationError("Nao registre pagamento em cobranca cancelada.")
            outros = cobranca.pagamentos.exclude(pk=self.pk).aggregate(
                total=models.Sum("valor")
            )["total"] or Decimal("0.00")
            if outros + self.valor > cobranca.valor:
                raise ValidationError("Pagamento ultrapassa o valor da cobranca.")
            super().save(*args, **kwargs)

    def __str__(self):
        return f"Pagamento de R$ {self.valor} - {self.data:%d/%m/%Y}"
