import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# Em produção as tabelas (veiculo, veiculo_foto, veiculo_foto_arquivo) já existem e são do ROUTE: managed=False.
# Só nos testes/dev local (CATALOGO_CRIAR_TABELAS=1) elas são criadas, num banco vazio.
GERENCIAR = getattr(settings, "CATALOGO_CRIAR_TABELAS", False)


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Veiculo",
            fields=[
                ("id", models.AutoField(db_column="topcrm_id", primary_key=True, serialize=False)),
                ("rid", models.CharField(db_column="id", max_length=20, unique=True)),
                ("placa", models.CharField(blank=True, max_length=10)),
                ("marca", models.CharField(max_length=60)),
                ("modelo", models.CharField(max_length=80)),
                ("versao", models.CharField(blank=True, max_length=80, null=True)),
                ("ano_modelo", models.PositiveIntegerField(db_column="ano")),
                ("km", models.PositiveIntegerField(blank=True, null=True)),
                ("valor_venda", models.DecimalField(db_column="preco", decimal_places=2, max_digits=10)),
                ("cor", models.CharField(blank=True, max_length=30, null=True)),
                ("cambio", models.CharField(blank=True, max_length=30, null=True)),
                ("combustivel", models.CharField(blank=True, max_length=30, null=True)),
                ("motor", models.CharField(blank=True, max_length=30, null=True)),
                ("opcionais", models.JSONField(blank=True, default=list, null=True)),
                ("itens_serie", models.JSONField(blank=True, default=list, null=True)),
                ("status", models.CharField(max_length=20)),
                ("unidade", models.CharField(blank=True, max_length=20, null=True)),
                ("data_venda", models.DateField(blank=True, null=True)),
                ("total_fotos", models.IntegerField(default=0)),
                ("atualizado_em", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "veiculo", "ordering": ["marca", "modelo"], "managed": GERENCIAR},
        ),
        migrations.CreateModel(
            name="FotoVeiculo",
            fields=[
                ("id", models.CharField(max_length=20, primary_key=True, serialize=False)),
                ("veiculo_rid", models.CharField(db_column="veiculo_id", db_index=True, max_length=20)),
                ("ordem", models.IntegerField(default=0)),
                ("capa", models.BooleanField(default=False)),
                ("largura", models.IntegerField(default=0)),
                ("altura", models.IntegerField(default=0)),
                ("bytes", models.IntegerField(default=0)),
                ("url", models.TextField(blank=True, null=True)),
                ("url_miniatura", models.TextField(blank=True, null=True)),
            ],
            options={"db_table": "veiculo_foto", "ordering": ["ordem", "id"], "managed": GERENCIAR},
        ),
        migrations.CreateModel(
            name="FotoArquivo",
            fields=[
                ("foto", models.OneToOneField(db_column="foto_id", db_constraint=False, on_delete=django.db.models.deletion.DO_NOTHING,
                                              primary_key=True, related_name="arquivo", serialize=False, to="catalogo.fotoveiculo")),
                ("jpeg", models.BinaryField(null=True)),
                ("miniatura", models.BinaryField(null=True)),
            ],
            options={"db_table": "veiculo_foto_arquivo", "managed": GERENCIAR},
        ),
    ]
