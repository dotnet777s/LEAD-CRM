import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from captacao.models import Lead
from catalogo.models import Veiculo


def _data(txt):
    if not txt:
        return None
    d = datetime.fromisoformat(str(txt).replace(" ", "T"))
    return d if timezone.is_aware(d) else timezone.make_aware(d, timezone.utc)


class Command(BaseCommand):
    help = ("SÓ DESENVOLVIMENTO LOCAL (SQLite): carrega a tabela estoque_veiculo a partir de um JSON exportado do estoque. "
            "Substitui os carros locais (inclusive os de demonstração).")

    def add_arguments(self, p):
        p.add_argument("arquivo")

    def handle(self, arquivo, **o):
        if not settings.CATALOGO_CRIAR_TABELAS:
            raise CommandError("Use só no SQLite local (CATALOGO_CRIAR_TABELAS=1). No Neon o estoque já existe e é só leitura.")
        try:
            with open(arquivo, encoding="utf-8-sig") as f:
                linhas = json.load(f)
        except (OSError, ValueError) as e:
            raise CommandError(f"Não consegui ler o arquivo: {e}")
        if not isinstance(linhas, list):
            raise CommandError("O JSON precisa ser uma lista de veículos.")

        novos = []
        for r in linhas:
            try:
                ficha = r.get("ficha") or {}
                if isinstance(ficha, str):
                    ficha = json.loads(ficha)
                novos.append(Veiculo(
                    id=int(r["id"]), marca=r["marca"], modelo=r["modelo"], versao=r.get("versao") or "",
                    ano_modelo=int(r["ano_modelo"]), placa=r["placa"], cor=r.get("cor") or "", km=int(r.get("km") or 0),
                    valor_venda=Decimal(str(r["valor_venda"])), status=r["status"],
                    possui_fotos=str(r.get("possui_fotos")).lower() in ("true", "1", "t"),
                    data_venda=(r.get("data_venda") or None), criado_em=_data(r.get("criado_em")) or timezone.now(),
                    atualizado_em=_data(r.get("atualizado_em")) or timezone.now(), ficha=ficha if isinstance(ficha, dict) else {}))
            except (KeyError, ValueError, InvalidOperation) as e:
                raise CommandError(f"Registro inválido (id={r.get('id')}): {e!r}")

        with transaction.atomic():
            demo = Lead.objects.filter(utm_campaign="demo")
            apagados = demo.count()
            demo.delete()                      # os leads de demonstração apontam para ids que vão mudar
            Veiculo.objects.all().delete()
            Veiculo.objects.bulk_create(novos)
        olinda = sum(1 for v in novos if v.status == "loja_olinda")
        self.stdout.write(f"{len(novos)} veículos importados ({olinda} em loja Olinda). Leads de demonstração removidos: {apagados}.")
