from django.db import migrations, models


PAPEIS = [
    ("gestor", "Gestor (acesso total, inclusive financeiro)"),
    ("equipe", "Equipe (edita CRM, projetos e tarefas; sem financeiro)"),
    ("leitura", "Somente leitura (sem financeiro)"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_groq_provider"),
    ]

    operations = [
        # Vinculos que ja existem continuam com acesso total.
        migrations.AddField(
            model_name="workspacemembership",
            name="papel",
            field=models.CharField(choices=PAPEIS, default="gestor", max_length=10, verbose_name="papel"),
        ),
        # Novos vinculos comecam como equipe.
        migrations.AlterField(
            model_name="workspacemembership",
            name="papel",
            field=models.CharField(choices=PAPEIS, default="equipe", max_length=10, verbose_name="papel"),
        ),
    ]
