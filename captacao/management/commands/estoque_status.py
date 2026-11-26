from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count

from catalogo.models import FotoVeiculo, Veiculo


class Command(BaseCommand):
    help = "Confere o estoque do CRM (vindo do AutoCerto): banco usado, carros disponíveis e fotos."

    def handle(self, *a, **o):
        db = connection.settings_dict
        self.stdout.write(f"Banco: {db['ENGINE'].split('.')[-1]} · {db.get('HOST') or db.get('NAME')}")
        try:
            total = Veiculo.objects.count()
        except Exception as e:  # tabela inexistente ou sem acesso
            self.stderr.write(f"Não consegui ler a tabela veiculo: {e}")
            if not settings.DATABASE_URL_DEFINIDA:
                self.stderr.write("DATABASE_URL não está definida: o app está usando o SQLite local (sem o estoque real).")
            return
        disp = Veiculo.objects.filter(status__in=Veiculo.EM_LOJA)
        por_local = ", ".join(f"{(r['unidade'] or '?')}: {r['n']}" for r in disp.values("unidade").annotate(n=Count("pk")).order_by("unidade"))
        self.stdout.write(f"Veículos no estoque: {total} · disponíveis: {disp.count()} ({por_local}) · com fotos: {disp.filter(total_fotos__gt=0).count()}")
        self.stdout.write(f"Fotos cadastradas: {FotoVeiculo.objects.count()}")
        sem_id = Veiculo.objects.filter(status__in=Veiculo.EM_LOJA, pk__isnull=True).count()
        if sem_id:
            self.stderr.write(f"{sem_id} carros sem topcrm_id: não têm página de captura.")
