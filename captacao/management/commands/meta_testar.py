from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from captacao import meta


class Command(BaseCommand):
    help = "Envia um evento de teste (test_event_code) para conferir em Gerenciador de Eventos > Testar eventos."

    def add_arguments(self, parser):
        parser.add_argument("--codigo", default="", help="test_event_code (padrão: META_TEST_EVENT_CODE).")
        parser.add_argument("--evento", default="PageView")

    def handle(self, *args, **o):
        codigo = o["codigo"] or settings.META_TEST_EVENT_CODE
        if not (settings.META_PIXEL_ID and settings.META_CAPI_ACCESS_TOKEN):
            raise CommandError("Defina META_PIXEL_ID e META_CAPI_ACCESS_TOKEN.")
        if not codigo:
            raise CommandError("Informe --codigo ou META_TEST_EVENT_CODE (aparece em 'Testar eventos').")
        status, corpo = meta.enviar_teste(codigo, o["evento"])
        self.stdout.write(f"HTTP {status}: {corpo}")
