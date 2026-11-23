"""
Espelho SOMENTE LEITURA do estoque do ROUTE (tabelas `veiculo`, `veiculo_foto`, `veiculo_foto_arquivo`).
managed=False: nenhuma migration daqui cria, altera ou apaga tabela do ROUTE.
Só mapeamos as colunas que o CRM usa. Nunca chame save()/delete() nestes models.

O id público do carro no CRM é o `topcrm_id` (inteiro, herdado do sistema antigo); o id de texto do ROUTE
(`rid`) liga as fotos ao carro.
"""
from django.db import models


def nome_proprio(txt):
    """'CHEVROLET ONIX LT2' -> 'Chevrolet Onix LT2' (palavras curtas ou com número ficam como estão: BYD, LT2, 1.0, AT)."""
    saida = []
    for w in (txt or "").split():
        saida.append(w.upper() if (len(w) <= 3 or any(c.isdigit() for c in w)) and w == w.upper() else w.capitalize() if w == w.upper() else w)
    return " ".join(saida)


class Veiculo(models.Model):
    DISPONIVEL = "disponivel"
    EM_LOJA = (DISPONIVEL,)     # Recife e Olinda são vendidos juntos; `unidade` só indica onde o carro está

    id = models.AutoField(primary_key=True, db_column="topcrm_id")
    rid = models.CharField(max_length=20, db_column="id", unique=True)
    placa = models.CharField(max_length=10, blank=True)
    marca = models.CharField(max_length=60)
    modelo = models.CharField(max_length=80)
    versao = models.CharField(max_length=80, blank=True, null=True)
    ano_modelo = models.PositiveIntegerField(db_column="ano")
    km = models.PositiveIntegerField(null=True, blank=True)
    valor_venda = models.DecimalField(max_digits=10, decimal_places=2, db_column="preco", null=True)
    fipe = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cor = models.CharField(max_length=30, blank=True, null=True)
    cambio = models.CharField(max_length=30, blank=True, null=True)
    combustivel = models.CharField(max_length=30, blank=True, null=True)
    motor = models.CharField(max_length=30, blank=True, null=True)
    opcionais = models.JSONField(default=list, blank=True, null=True)
    itens_serie = models.JSONField(default=list, blank=True, null=True)
    status = models.CharField(max_length=20)          # disponivel | preparacao | vendido
    unidade = models.CharField(max_length=20, blank=True, null=True)    # Olinda | Recife
    data_venda = models.DateField(null=True, blank=True)
    total_fotos = models.IntegerField(default=0)
    atualizado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "veiculo"
        ordering = ["marca", "modelo"]

    def __str__(self):
        return f"{self.marca} {self.modelo} {self.ano_modelo}"

    @property
    def ano(self):
        """Ano-modelo com 4 dígitos ('27' vira 2027; '1819' vira 2019)."""
        a = self.ano_modelo
        if a < 100:
            return 2000 + a
        if 1000 <= a <= 9999:
            fab, mod = divmod(a, 100)
            if mod in (fab, fab + 1):
                return 2000 + mod
        return a

    @property
    def nome(self):
        return f"{self.marca_fmt} {self.modelo_fmt} {self.versao_fmt}".strip()

    marca_fmt = property(lambda self: nome_proprio(self.marca))
    modelo_fmt = property(lambda self: nome_proprio(self.modelo))
    versao_fmt = property(lambda self: nome_proprio(self.versao))
    cor_fmt = property(lambda self: nome_proprio(self.cor))

    @property
    def possui_fotos(self):
        return (self.total_fotos or 0) > 0

    @property
    def em_loja(self):
        return self.status in self.EM_LOJA

    @property
    def local(self):
        """'Recife', 'Olinda' ou ''."""
        return (self.unidade or "").strip().title()


class FotoVeiculo(models.Model):
    """Foto do carro (só leitura). Os bytes ficam em FotoArquivo (ou em `url`, se o ROUTE passar a usar um bucket)."""
    id = models.CharField(primary_key=True, max_length=20)
    veiculo_rid = models.CharField(max_length=20, db_column="veiculo_id", db_index=True)
    ordem = models.IntegerField(default=0)
    capa = models.BooleanField(default=False)
    largura = models.IntegerField(default=0)
    altura = models.IntegerField(default=0)
    bytes = models.IntegerField(default=0)
    url = models.TextField(blank=True, null=True)
    url_miniatura = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "veiculo_foto"
        ordering = ["ordem", "id"]


class FotoArquivo(models.Model):
    foto = models.OneToOneField(FotoVeiculo, primary_key=True, db_column="foto_id", on_delete=models.DO_NOTHING,
                                db_constraint=False, related_name="arquivo")
    jpeg = models.BinaryField(null=True)
    miniatura = models.BinaryField(null=True)

    class Meta:
        managed = False
        db_table = "veiculo_foto_arquivo"
