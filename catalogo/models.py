"""
Estoque do CRM, alimentado pelo XML de anúncios do AutoCerto (veja catalogo/autocerto.py).

São tabelas PRÓPRIAS do CRM (estoque_*, no esquema do CRM). As tabelas do ROUTE (`veiculo`, `veiculo_foto`...) não são mais
lidas nem alteradas por aqui. O id público do carro (`id`, usado em /v/<id>-slug/) é preservado para os carros que já
existiam no ROUTE (casados pela placa), para não quebrar links de anúncios.
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
    RETIRADO = "retirado"          # saiu do XML do AutoCerto (vendido ou despublicado)
    EM_LOJA = (DISPONIVEL,)

    rid = models.CharField("Id no AutoCerto", max_length=20, unique=True)
    placa = models.CharField(max_length=10, blank=True)
    marca = models.CharField(max_length=60)
    modelo = models.CharField(max_length=80)
    versao = models.CharField(max_length=120, blank=True, null=True)
    ano_modelo = models.PositiveIntegerField()
    km = models.PositiveIntegerField(null=True, blank=True)
    valor_venda = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    fipe = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cor = models.CharField(max_length=30, blank=True, null=True)
    cambio = models.CharField(max_length=30, blank=True, null=True)
    combustivel = models.CharField(max_length=30, blank=True, null=True)
    motor = models.CharField(max_length=30, blank=True, null=True)
    portas = models.PositiveSmallIntegerField(null=True, blank=True)
    zero_km = models.BooleanField(default=False)
    opcionais = models.JSONField(default=list, blank=True, null=True)
    itens_serie = models.JSONField(default=list, blank=True, null=True)
    observacoes = models.TextField(blank=True, default="")
    video_url = models.CharField(max_length=300, blank=True, default="")
    status = models.CharField(max_length=20, default=DISPONIVEL)          # disponivel | retirado
    unidade = models.CharField(max_length=20, blank=True, null=True)
    data_venda = models.DateField(null=True, blank=True)
    total_fotos = models.IntegerField(default=0)
    atualizado_em = models.DateTimeField(null=True, blank=True)
    cadastrado_em = models.DateTimeField(null=True, blank=True)
    sincronizado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "estoque_veiculo"
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
    def cambio_fmt(self):
        """'AUTOMATICO' -> 'Automático'; 'manual de 6 marchas' -> 'Manual'; vazio se não informado."""
        c = (self.cambio or "").strip().lower()
        if not c:
            return ""
        for chave, nome in (("automatizad", "Automatizado"), ("autom", "Automático"), ("cvt", "CVT"), ("manual", "Manual")):
            if chave in c:
                return nome
        return nome_proprio(self.cambio)

    @property
    def possui_fotos(self):
        return (self.total_fotos or 0) > 0

    @property
    def em_loja(self):
        return self.status in self.EM_LOJA

    @property
    def local(self):
        """'Recife', 'Olinda' ou '' (o AutoCerto não informa a unidade)."""
        return (self.unidade or "").strip().title()


class FotoVeiculo(models.Model):
    """Foto do carro. `url` aponta para a imagem no AutoCerto; os arquivos redimensionados ficam em cache em disco."""
    id = models.CharField(primary_key=True, max_length=24)
    veiculo_rid = models.CharField(max_length=20, db_index=True)
    ordem = models.IntegerField(default=0)
    capa = models.BooleanField(default=False)
    largura = models.IntegerField(default=0)
    altura = models.IntegerField(default=0)
    bytes = models.IntegerField(default=0)
    url = models.TextField(blank=True, null=True)
    url_miniatura = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "estoque_foto"
        ordering = ["ordem", "id"]


class FotoArquivo(models.Model):
    """Reservado: bytes da foto guardados no banco (não usado com o AutoCerto, que serve por URL)."""
    foto = models.OneToOneField(FotoVeiculo, primary_key=True, on_delete=models.CASCADE, related_name="arquivo")
    jpeg = models.BinaryField(null=True)
    miniatura = models.BinaryField(null=True)

    class Meta:
        db_table = "estoque_foto_arquivo"
