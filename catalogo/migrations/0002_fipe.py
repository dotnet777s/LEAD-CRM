from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalogo", "0001_initial")]

    operations = [
        migrations.AddField(model_name="veiculo", name="fipe",
                            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
        migrations.AlterField(model_name="veiculo", name="valor_venda",
                              field=models.DecimalField(db_column="preco", decimal_places=2, max_digits=10, null=True)),
    ]
