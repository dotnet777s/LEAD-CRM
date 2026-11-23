import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# Em produção as tabelas estoque_* já existem e são de outro sistema: managed=False (nada é criado).
# Só nos testes (settings_test) elas são criadas, num banco vazio, para podermos testar.
GERENCIAR = getattr(settings, "CATALOGO_CRIAR_TABELAS", False)


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Veiculo",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ("marca", models.CharField(max_length=60)),
                ("modelo", models.CharField(max_length=80)),
                ("versao", models.CharField(blank=True, max_length=80)),
                ("ano_modelo", models.PositiveIntegerField()),
                ("placa", models.CharField(max_length=8)),
                ("cor", models.CharField(blank=True, max_length=30)),
                ("km", models.PositiveIntegerField(default=0)),
                ("valor_venda", models.DecimalField(decimal_places=2, max_digits=10)),
                ("status", models.CharField(max_length=20)),
                ("possui_fotos", models.BooleanField(default=False)),
                ("data_venda", models.DateField(blank=True, null=True)),
                ("criado_em", models.DateTimeField()),
                ("atualizado_em", models.DateTimeField()),
                ("ficha", models.JSONField(blank=True, default=dict)),
            ],
            options={"db_table": "estoque_veiculo", "ordering": ["-criado_em"], "managed": GERENCIAR},
        ),
        migrations.CreateModel(
            name="TakeVeiculo",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ("arquivo", models.CharField(max_length=500)),
                ("miniatura", models.CharField(blank=True, max_length=500)),
                ("ordem", models.PositiveSmallIntegerField(default=0)),
                ("largura", models.PositiveIntegerField(default=0)),
                ("altura", models.PositiveIntegerField(default=0)),
                ("categoria", models.CharField(blank=True, max_length=40)),
                ("destaque", models.CharField(blank=True, max_length=80)),
                ("foto", models.BooleanField(default=False)),
                ("ativo", models.BooleanField(default=True)),
                ("veiculo", models.ForeignKey(db_constraint=False, on_delete=django.db.models.deletion.DO_NOTHING,
                                              related_name="takes", to="catalogo.veiculo")),
            ],
            options={"db_table": "estoque_takeveiculo", "ordering": ["ordem", "pk"], "managed": GERENCIAR},
        ),
    ]
