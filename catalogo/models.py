"""
Espelho SOMENTE LEITURA das tabelas do estoque (outro sistema é o dono delas).
managed=False: nenhuma migration daqui cria, altera ou apaga tabela estoque_*.
Só mapeamos as colunas que o CRM usa. Nunca chame save()/delete() nestes models.
"""
from django.db import models


def nome_proprio(txt):
    """'CHEVROLET ONIX LT2' -> 'Chevrolet Onix LT2' (palavras curtas ou com número ficam como estão: BYD, LT2, 1.0, AT)."""
    saida = []
    for w in (txt or "").split():
        saida.append(w.upper() if (len(w) <= 3 or any(c.isdigit() for c in w)) and w == w.upper() else w.capitalize() if w == w.upper() else w)
    return " ".join(saida)


class Veiculo(models.Model):
    class Status(models.TextChoices):
        PREPARACAO = "preparacao", "Em preparação"
        LOJA_RECIFE = "loja_recife", "Em loja (Recife)"
        LOJA_OLINDA = "loja_olinda", "Em loja (Olinda)"
        VENDIDO = "vendido", "Vendido"

    EM_LOJA = ("loja_recife", "loja_olinda")     # os dois estoques são vendidos juntos; o status só indica onde o carro está

    marca = models.CharField(max_length=60)
    modelo = models.CharField(max_length=80)
    versao = models.CharField(max_length=80, blank=True)
    ano_modelo = models.PositiveIntegerField()
    placa = models.CharField(max_length=8)
    cor = models.CharField(max_length=30, blank=True)
    km = models.PositiveIntegerField(default=0)
    valor_venda = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices)
    possui_fotos = models.BooleanField(default=False)
    data_venda = models.DateField(null=True, blank=True)
    criado_em = models.DateTimeField()
    atualizado_em = models.DateTimeField()
    ficha = models.JSONField(default=dict, blank=True)

    class Meta:
        managed = False
        db_table = "estoque_veiculo"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.marca} {self.modelo} {self.ano_modelo}"

    @property
    def ano(self):
        """Ano-modelo com 4 dígitos ('27' vira 2027; '1819' vira 2019), igual ao sistema do estoque."""
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
    def em_loja(self):
        return self.status in self.EM_LOJA


class TakeVeiculo(models.Model):
    """Fotos/vídeos do veículo (só leitura). Na tabela real `destaque` é texto, não flag."""
    veiculo = models.ForeignKey(Veiculo, related_name="takes", on_delete=models.DO_NOTHING, db_constraint=False)
    arquivo = models.CharField(max_length=500)
    miniatura = models.CharField(max_length=500, blank=True)
    ordem = models.PositiveSmallIntegerField(default=0)
    largura = models.PositiveIntegerField(default=0)
    altura = models.PositiveIntegerField(default=0)
    categoria = models.CharField(max_length=40, blank=True)
    destaque = models.CharField(max_length=80, blank=True)
    foto = models.BooleanField(default=False)
    ativo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = "estoque_takeveiculo"
        ordering = ["ordem", "pk"]
