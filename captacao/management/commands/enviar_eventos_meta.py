import time

from django.core.management.base import BaseCommand

from captacao import meta


class Command(BaseCommand):
    help = "Worker da Conversions API: envia os eventos pendentes do outbox (com retry exponencial)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Fica rodando (use no servidor/PC da loja).")
        parser.add_argument("--intervalo", type=int, default=5, help="Segundos entre ciclos no modo --loop.")

    def handle(self, *args, **o):
        while True:
            r = meta.rodar_lote()
            if r.get("desligado"):
                self.stdout.write("CAPI desligada (META_CAPI_ENABLED/PIXEL_ID/TOKEN). Nada enviado.")
            elif any(r.values()):
                self.stdout.write(f"enviados={r['enviados']} erros={r['erros']} pendentes={r['pendentes']} descartados={r['descartados']}")
            if not o["loop"]:
                return
            time.sleep(o["intervalo"])
