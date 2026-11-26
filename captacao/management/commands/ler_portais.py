import time

from django.core.management.base import BaseCommand

from captacao import portais


class Command(BaseCommand):
    help = "Lê a caixa de e-mail dos portais (Napista, Mobiauto, OLX) e cria os leads."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Fica rodando (serviço no servidor).")
        parser.add_argument("--intervalo", type=int, default=60, help="Segundos entre leituras no modo --loop.")

    def handle(self, *args, **o):
        while True:
            try:
                r = portais.ler_caixa()
            except Exception as e:  # noqa: BLE001 - rede/credencial: tenta de novo no próximo ciclo
                self.stderr.write(f"Falha ao ler a caixa: {e}")
                r = "erro"
            if r is None:
                self.stdout.write("Leitura desligada (defina PORTAIS_IMAP_HOST, PORTAIS_IMAP_USER e PORTAIS_IMAP_PASS).")
            elif r != "erro" and any(r):
                self.stdout.write(f"processados={r[0]} erros={r[1]}")
            if not o["loop"]:
                return
            time.sleep(o["intervalo"])
