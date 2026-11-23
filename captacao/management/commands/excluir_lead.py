from django.core.management.base import BaseCommand, CommandError

from captacao import services, utils
from captacao.models import Lead


class Command(BaseCommand):
    help = "LGPD: apaga os dados de um lead a pedido dele (por telefone)."

    def add_arguments(self, parser):
        parser.add_argument("telefone")

    def handle(self, *args, **o):
        tel = utils.normalizar_telefone(o["telefone"])
        leads = list(Lead.objects.filter(telefone=tel)) if tel else []
        if not leads:
            raise CommandError("Nenhum lead com esse telefone.")
        for l in leads:
            services.excluir_dados_do_lead(l)
        self.stdout.write(f"{len(leads)} lead(s) excluído(s).")
