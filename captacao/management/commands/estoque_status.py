from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

from catalogo.models import TakeVeiculo, Veiculo


class Command(BaseCommand):
    help = "Confere a conexão com o estoque (somente leitura): banco usado e quantos carros há em Olinda."

    def handle(self, *a, **o):
        db = connection.settings_dict
        self.stdout.write(f"Banco: {db['ENGINE'].split('.')[-1]} · {db.get('HOST') or db.get('NAME')}")
        try:
            total = Veiculo.objects.count()
        except Exception as e:  # tabela inexistente ou sem acesso
            self.stderr.write(f"Não consegui ler estoque_veiculo: {e}")
            if not settings.DATABASE_URL_DEFINIDA:
                self.stderr.write("DATABASE_URL não está definida: o app está usando o SQLite local (sem o estoque real).")
            return
        olinda = Veiculo.objects.filter(status="loja_olinda").count()
        com_fotos = Veiculo.objects.filter(status="loja_olinda", possui_fotos=True).count()
        self.stdout.write(f"Veículos no estoque: {total} · em loja Olinda: {olinda} · com fotos: {com_fotos}")
        self.stdout.write(f"Fotos/vídeos cadastrados: {TakeVeiculo.objects.count()}")
