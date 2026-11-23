from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from captacao import services
from captacao.models import Lead
from catalogo.models import Veiculo


class Command(BaseCommand):
    help = "SÓ PARA DESENVOLVIMENTO LOCAL (SQLite): cria carros e leads de exemplo."

    def handle(self, *a, **o):
        if not settings.CATALOGO_CRIAR_TABELAS:
            raise CommandError("Use só no SQLite local com CATALOGO_CRIAR_TABELAS=1 (nunca no banco real).")
        agora = timezone.now()
        carros = []
        for i, (m, mo, ano, preco) in enumerate([("Fiat", "Argo", 2022, 59900), ("VW", "Polo", 2021, 64900),
                                                 ("Chevrolet", "Onix", 2022, 62000)]):
            v, _ = Veiculo.objects.get_or_create(placa=f"DEM{i}A00", defaults=dict(
                rid=f"pdemo000{i}",
                marca=m, modelo=mo, versao="1.0 Flex", ano_modelo=ano, km=30000 + i * 9000, valor_venda=Decimal(preco),
                status="disponivel", unidade="Olinda", total_fotos=1, atualizado_em=agora,
                itens_serie=["Ar-condicionado", "Direção elétrica", "Central multimídia"], opcionais=["Único dono", "Revisado"]))
            carros.append(v)
        u = get_user_model().objects.filter(is_superuser=True).first()
        casos = [("Maria Silva", "81999990001", "a_vista", "sem", "semana", 2),
                 ("João Pereira", "81999990002", "financiado", "10_30", "30_dias", 20),
                 ("Ana Costa", "81999990003", "financiado", "ate_10", "semana", 90),
                 ("Pedro Lima", "81999990004", "consorcio", "sem", "pesquisando", 300)]
        for i, (nome, tel, pg, ent, pz, atras) in enumerate(casos):
            if Lead.objects.filter(telefone="55" + tel).exists():
                continue
            lead, _ = services.capturar_lead(
                carros[i % 3], nome, "55" + tel, {"pagamento": pg, "entrada": ent, "troca": "nao", "prazo": pz},
                {"utm_content": f"anuncio-{i % 2 + 1}", "utm_campaign": "demo", "ip": "127.0.0.1", "event_id": f"{i:032x}"},
                agora=agora - timedelta(minutes=atras))
            if i == 2 and u:
                services.mudar_etapa(lead, "atendimento", u)
        self.stdout.write("Demo criada. Páginas: " + ", ".join(f"/v/{v.pk}/" for v in carros))
