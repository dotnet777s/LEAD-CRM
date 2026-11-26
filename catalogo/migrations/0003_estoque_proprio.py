import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """O estoque deixa de ser o espelho (somente leitura) do ROUTE e passa a ter tabelas próprias do CRM (estoque_*),
    alimentadas pelo XML do AutoCerto. Apagar os modelos antigos NÃO toca nas tabelas do ROUTE: eles eram managed=False."""

    dependencies = [("catalogo", "0002_fipe")]

    operations = [
        migrations.DeleteModel(name="FotoArquivo"),
        migrations.DeleteModel(name="FotoVeiculo"),
        migrations.DeleteModel(name="Veiculo"),
        migrations.CreateModel(
            name="Veiculo",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("rid", models.CharField(max_length=20, unique=True, verbose_name="Id no AutoCerto")),
                ("placa", models.CharField(blank=True, max_length=10)),
                ("marca", models.CharField(max_length=60)),
                ("modelo", models.CharField(max_length=80)),
                ("versao", models.CharField(blank=True, max_length=120, null=True)),
                ("ano_modelo", models.PositiveIntegerField()),
                ("km", models.PositiveIntegerField(blank=True, null=True)),
                ("valor_venda", models.DecimalField(decimal_places=2, max_digits=10, null=True)),
                ("fipe", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("cor", models.CharField(blank=True, max_length=30, null=True)),
                ("cambio", models.CharField(blank=True, max_length=30, null=True)),
                ("combustivel", models.CharField(blank=True, max_length=30, null=True)),
                ("motor", models.CharField(blank=True, max_length=30, null=True)),
                ("portas", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("zero_km", models.BooleanField(default=False)),
                ("opcionais", models.JSONField(blank=True, default=list, null=True)),
                ("itens_serie", models.JSONField(blank=True, default=list, null=True)),
                ("observacoes", models.TextField(blank=True, default="")),
                ("video_url", models.CharField(blank=True, default="", max_length=300)),
                ("status", models.CharField(default="disponivel", max_length=20)),
                ("unidade", models.CharField(blank=True, max_length=20, null=True)),
                ("data_venda", models.DateField(blank=True, null=True)),
                ("total_fotos", models.IntegerField(default=0)),
                ("atualizado_em", models.DateTimeField(blank=True, null=True)),
                ("cadastrado_em", models.DateTimeField(blank=True, null=True)),
                ("sincronizado_em", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "estoque_veiculo", "ordering": ["marca", "modelo"]},
        ),
        migrations.CreateModel(
            name="FotoVeiculo",
            fields=[
                ("id", models.CharField(max_length=24, primary_key=True, serialize=False)),
                ("veiculo_rid", models.CharField(db_index=True, max_length=20)),
                ("ordem", models.IntegerField(default=0)),
                ("capa", models.BooleanField(default=False)),
                ("largura", models.IntegerField(default=0)),
                ("altura", models.IntegerField(default=0)),
                ("bytes", models.IntegerField(default=0)),
                ("url", models.TextField(blank=True, null=True)),
                ("url_miniatura", models.TextField(blank=True, null=True)),
            ],
            options={"db_table": "estoque_foto", "ordering": ["ordem", "id"]},
        ),
        migrations.CreateModel(
            name="FotoArquivo",
            fields=[
                ("foto", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name="arquivo",
                                              serialize=False, to="catalogo.fotoveiculo")),
                ("jpeg", models.BinaryField(null=True)),
                ("miniatura", models.BinaryField(null=True)),
            ],
            options={"db_table": "estoque_foto_arquivo"},
        ),
    ]
