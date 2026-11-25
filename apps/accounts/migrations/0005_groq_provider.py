from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_perfilusuario")]

    operations = [
        migrations.AddField(
            model_name="integracaogemini",
            name="provedor",
            field=models.CharField(
                choices=[("gemini", "Gemini (legado)"), ("groq", "Groq")],
                default="gemini",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="integracaogemini",
            name="provedor",
            field=models.CharField(
                choices=[("gemini", "Gemini (legado)"), ("groq", "Groq")],
                default="groq",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="workspacemembership",
            name="pode_gerenciar_gemini",
            field=models.BooleanField(default=False, verbose_name="pode gerenciar a integracao de IA desta empresa"),
        ),
    ]