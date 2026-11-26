from django.core.management.base import BaseCommand, CommandError

from catalogo import autocerto


class Command(BaseCommand):
    help = "Sincroniza o estoque com o XML do AutoCerto (AUTOCERTO_URL) e prepara as capas do catálogo."

    def add_arguments(self, p):
        p.add_argument("--arquivo", help="Usa um XML salvo em vez de baixar (teste).")
        p.add_argument("--sem-limpar", action="store_true", help="Não desfaz vínculos de leads com carros que não existem mais (use antes do deploy).")
        p.add_argument("--sem-fotos", action="store_true", help="Não baixa as capas agora.")

    def handle(self, *a, **o):
        try:
            conteudo = open(o["arquivo"], "rb").read() if o["arquivo"] else None
            r = autocerto.sincronizar(conteudo, limpar=not o["sem_limpar"])
        except autocerto.ErroEstoque as e:
            raise CommandError(str(e))
        self.stdout.write(f"Estoque: {r['total']} no XML · {r['novos']} novos · {r['atualizados']} atualizados · {r['retirados']} retirados.")
        if not o["sem_fotos"]:
            self.stdout.write(f"Capas prontas: {autocerto.aquecer_capas()}")
