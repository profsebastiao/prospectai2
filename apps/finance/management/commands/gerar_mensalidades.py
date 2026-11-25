from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.finance.models import Contrato, Cobranca


def avancar_mes(ano, mes):
    if mes == 12:
        return ano + 1, 1
    return ano, mes + 1


def competencias(inicio, quantidade):
    ano, mes = inicio.year, inicio.month

    for _ in range(quantidade):
        yield date(ano, mes, 1)
        ano, mes = avancar_mes(ano, mes)


def vencimento_contrato(contrato, competencia):
    ultimo = monthrange(
        competencia.year, competencia.month
    )[1]

    dia = min(contrato.dia_vencimento, ultimo)

    vencimento = date(
        competencia.year,
        competencia.month,
        dia
    )

    # Nao gerar vencimento anterior ao inicio do contrato.
    if vencimento < contrato.inicio:
        vencimento = contrato.inicio

    return vencimento


class Command(BaseCommand):
    help = (
        "Gera cobrancas mensais a partir de contratos. "
        "Executa simulacao por padrao; use --aplicar para gravar."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--mes",
            help="Mes inicial no formato AAAA-MM"
        )
        parser.add_argument(
            "--meses",
            type=int,
            default=1,
            help="Quantidade de meses (1 a 24)"
        )
        parser.add_argument(
            "--workspace", type=int, help="ID do espaco de trabalho"
        )
        parser.add_argument(
            "--contrato",
            type=int,
            help="ID de um contrato especifico"
        )
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Gravar as novas cobrancas no SQLite"
        )

    def handle(self, *args, **options):
        hoje = timezone.localdate()

        if options["mes"]:
            try:
                ano, mes = map(
                    int, options["mes"].split("-")
                )
                inicio = date(ano, mes, 1)
            except (TypeError, ValueError):
                raise CommandError(
                    "Informe --mes no formato AAAA-MM."
                )
        else:
            inicio = hoje.replace(day=1)

        quantidade = options["meses"]
        if not 1 <= quantidade <= 24:
            raise CommandError(
                "--meses deve estar entre 1 e 24."
            )

        contratos = Contrato.objects.filter(
            ativo=True,
            periodicidade="mensal"
        ).order_by("pk")

        if options["workspace"] is not None:
            contratos = contratos.filter(cliente__workspace_id=options["workspace"])

        if options["contrato"] is not None:
            contratos = contratos.filter(
                pk=options["contrato"]
            )
            if not contratos.exists():
                raise CommandError(
                    "Contrato mensal ativo nao encontrado."
                )

        aplicar = options["aplicar"]

        self.stdout.write(
            "MODO: " + (
                "GRAVACAO" if aplicar else "SIMULACAO"
            )
        )

        criadas = 0
        existentes = 0
        ignoradas = 0
        planejadas = 0

        for competencia in competencias(
            inicio, quantidade
        ):
            ultimo_dia = date(
                competencia.year,
                competencia.month,
                monthrange(
                    competencia.year,
                    competencia.month
                )[1]
            )

            for contrato in contratos:
                # Contrato ainda nao iniciado.
                if contrato.inicio > ultimo_dia:
                    ignoradas += 1
                    continue

                # Contrato encerrado antes do mes.
                if (
                    contrato.termino
                    and contrato.termino < competencia
                ):
                    ignoradas += 1
                    continue

                vencimento = vencimento_contrato(
                    contrato, competencia
                )

                # O vencimento nao pode ultrapassar
                # o termino do contrato.
                if (
                    contrato.termino
                    and vencimento > contrato.termino
                ):
                    ignoradas += 1
                    continue

                identificacao = (
                    f"Contrato {contrato.pk}, "
                    f"competencia "
                    f"{competencia:%m/%Y}"
                )

                if Cobranca.objects.filter(
                    contrato=contrato,
                    competencia=competencia
                ).exists():
                    existentes += 1
                    self.stdout.write(
                        "EXISTENTE: " + identificacao
                    )
                    continue

                if not aplicar:
                    planejadas += 1
                    self.stdout.write(
                        "PREVISTA: " + identificacao
                        + f", vencimento {vencimento:%d/%m/%Y}"
                        + f", valor R$ {contrato.valor}"
                    )
                    continue

                # A restricao unica protege contra
                # duas cobrancas da mesma competencia.
                try:
                    with transaction.atomic():
                        _, criada = (
                            Cobranca.objects.get_or_create(
                                contrato=contrato,
                                competencia=competencia,
                                defaults={
                                    "vencimento": vencimento,
                                    "valor": contrato.valor,
                                    "situacao": "aberta",
                                },
                            )
                        )
                except IntegrityError as erro:
                    raise CommandError(
                        "Conflito de integridade na cobranca "
                        + identificacao
                    ) from erro

                if criada:
                    criadas += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            "CRIADA: " + identificacao
                        )
                    )
                else:
                    existentes += 1
                    self.stdout.write(
                        "EXISTENTE: " + identificacao
                    )

        self.stdout.write("")
        self.stdout.write(
            f"Previstas: {planejadas} | "
            f"Criadas: {criadas} | "
            f"Ja existentes: {existentes} | "
            f"Ignoradas: {ignoradas}"
        )

        if not aplicar:
            self.stdout.write(
                self.style.WARNING(
                    "Simulacao concluida. "
                    "Nenhuma cobranca foi gravada."
                )
            )
