"""Campanhas (8 tipos, como no Avise Aí), sempre com um modelo aprovado e respeitando o limite diário da conta.

Estáticas (criam a lista na hora; você revisa e inicia): todos os contatos, por etiqueta, por etapa do funil,
por leads cadastrados e item do catálogo. Automáticas (ficam "ativas" e agem sozinhas): régua de disparos (a etiqueta
entra -> mensagens nos dias definidos), data de aniversário e "avise quando chegar".
Segurança: nunca envia a quem pediu para não receber, só usa modelo APROVADO, envia aos poucos e para no limite diário."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from . import funil, utils, whatsapp
from .models import Campanha, CampanhaEnvio, Lead, ModeloWhatsApp, Procura

LOTE = 15
ESTATICAS = (Campanha.Tipo.TODOS, Campanha.Tipo.TAG, Campanha.Tipo.ETAPA, Campanha.Tipo.CADASTRO, Campanha.Tipo.ITEM)
AUTOMATICAS = (Campanha.Tipo.REGUA, Campanha.Tipo.ANIVERSARIO, Campanha.Tipo.AVISE)


def publico(filtro):
    """Leads da campanha. Filtros: todos (inclui vendidos/perdidos), etapas, origem, portal, etiqueta(s), de/ate (cadastro), dias_parado."""
    qs = Lead.objects.filter(nao_contatar=False).exclude(telefone="")
    etapas = [e for e in (filtro.get("etapas") or []) if e in funil.chaves()]
    if etapas:
        qs = qs.filter(etapa__in=etapas)
    elif not filtro.get("todos"):
        qs = qs.exclude(etapa__in=funil.fechadas())
    if filtro.get("origem") in Lead.Origem.values:
        qs = qs.filter(origem=filtro["origem"])
    if filtro.get("portal") in Lead.Portal.values:
        qs = qs.filter(portal=filtro["portal"])
    ids = [i for i in ([filtro.get("etiqueta")] + list(filtro.get("etiquetas") or [])) if str(i or "").isdigit()]
    if ids:
        qs = qs.filter(etiquetas__pk__in=[int(i) for i in ids])
    if filtro.get("de"):
        qs = qs.filter(criado_em__date__gte=filtro["de"])
    if filtro.get("ate"):
        qs = qs.filter(criado_em__date__lte=filtro["ate"])
    dias = int(filtro.get("dias_parado") or 0)
    if dias > 0:
        qs = qs.filter(atualizado_em__lte=timezone.now() - timedelta(days=dias))
    return qs.distinct().order_by("-criado_em")


def _janela_do_dia(agora, hora_ini=9, hora_fim=18):
    """Intervalo de hoje (ou de amanhã, se já passou) onde as mensagens são espalhadas."""
    loc = timezone.localtime(agora)
    ini = loc.replace(hour=hora_ini, minute=0, second=0, microsecond=0)
    fim = loc.replace(hour=hora_fim, minute=0, second=0, microsecond=0)
    if loc >= fim:
        ini, fim = ini + timedelta(days=1), fim + timedelta(days=1)
    return max(ini, loc), fim


def _dados_do_carro(v):
    return {"carro": f"{v.marca_fmt} {v.modelo_fmt} {v.ano}", "preco": utils.preco_brl(v.valor_venda),
            "link": settings.SITE_URL + reverse("captacao:captura", args=[v.pk, utils.slug_veiculo(v)])}


def _extras_item(config):
    from catalogo.models import Veiculo
    vid = str(config.get("veiculo_id") or "")
    v = Veiculo.objects.filter(pk=int(vid)).first() if vid.isdigit() else None
    return _dados_do_carro(v) if v else {}


@transaction.atomic
def criar(nome, modelo, filtro, usuario=None, tipo="etapa", config=None, espalhar=False):
    """Cria a campanha. Estáticas já nascem com a lista de envios (rascunho); automáticas nascem sem lista (inativas)."""
    config = dict(config or {})
    c = Campanha.objects.create(nome=nome[:100], modelo=modelo, filtro=filtro, tipo=tipo, config=config, criada_por=usuario)
    if tipo in ESTATICAS:
        leads = list(publico(filtro)[:2000])
        extras = _extras_item(config) if tipo == Campanha.Tipo.ITEM else {}
        if espalhar and leads:
            ini, fim = _janela_do_dia(timezone.now())
            intervalo = (fim - ini) / len(leads)
        CampanhaEnvio.objects.bulk_create([
            CampanhaEnvio(campanha=c, lead=l, extras=extras, enviar_em=(ini + intervalo * i) if (espalhar and leads) else None)
            for i, l in enumerate(leads)])
    return c


def numeros(c):
    q = c.envios
    return {"total": q.count(), "enviados": q.filter(status="enviado").count(), "falhas": q.filter(status="falhou").count(),
            "fila": q.filter(status="pendente").count()}


def _modelo_do_passo(passo):
    pid = str(passo.get("modelo") or "")
    return ModeloWhatsApp.objects.filter(pk=int(pid)).first() if pid.isdigit() else None


def iniciar(c, agora=None):
    usados = [c.modelo] + [m for m in (_modelo_do_passo(p) for p in c.config.get("passos", [])) if m]
    if any(not m.aprovado for m in usados):
        raise whatsapp.WhatsAppErro("O modelo ainda não foi aprovado pela Meta." if len(usados) == 1
                                    else "Todos os modelos da campanha precisam estar aprovados pela Meta.")
    if c.tipo in ESTATICAS and not c.envios.filter(status="pendente").exists():
        raise whatsapp.WhatsAppErro("Não há ninguém na fila desta campanha.")
    c.status, c.iniciada_em = Campanha.Status.ENVIANDO, c.iniciada_em or (agora or timezone.now())
    c.save(update_fields=["status", "iniciada_em"])


def pausar(c):
    if c.status == Campanha.Status.ENVIANDO:
        c.status = Campanha.Status.PAUSADA
        c.save(update_fields=["status"])


def enviados_24h(agora=None):
    from .models import Aviso
    agora = agora or timezone.now()
    corte = agora - timedelta(hours=24)
    return (CampanhaEnvio.objects.filter(status="enviado", enviado_em__gte=corte).count()
            + Aviso.objects.filter(status="enviado", enviado_em__gte=corte).count())


# ---------------------------------------------------------------- régua: quem recebe a etiqueta entra no cronograma
def ao_etiquetar(lead, etiqueta, agora=None):
    """Chamado quando uma etiqueta é posta no lead (à mão ou pelas regras). Inscreve na régua das campanhas ATIVAS."""
    agora = agora or timezone.now()
    if lead.nao_contatar:
        return 0
    n = 0
    for c in Campanha.objects.filter(tipo=Campanha.Tipo.REGUA, status=Campanha.Status.ENVIANDO):
        if etiqueta.pk not in [int(i) for i in c.config.get("etiquetas", []) if str(i).isdigit()]:
            continue
        for i, p in enumerate(c.config.get("passos", []), start=1):
            quando = agora + timedelta(days=int(p.get("dias") or 0))
            _, criou = CampanhaEnvio.objects.get_or_create(campanha=c, lead=lead, passo=i, defaults={"modelo": _modelo_do_passo(p), "enviar_em": quando})
            n += criou
    return n


# ---------------------------------------------------------------- automáticas diárias
def rodar_automaticas(agora=None):
    """Aniversariantes do dia e 'avise quando chegar'. Chamado pelo worker (idempotente)."""
    agora = agora or timezone.now()
    hoje = timezone.localtime(agora).date()
    n = 0
    for c in Campanha.objects.filter(tipo=Campanha.Tipo.ANIVERSARIO, status=Campanha.Status.ENVIANDO):
        for l in Lead.objects.filter(aniversario__month=hoje.month, aniversario__day=hoje.day, nao_contatar=False):
            _, criou = CampanhaEnvio.objects.get_or_create(campanha=c, lead=l, passo=hoje.year, defaults={"enviar_em": agora})
            n += criou
    for c in Campanha.objects.filter(tipo=Campanha.Tipo.AVISE, status=Campanha.Status.ENVIANDO):
        n += _avisar_chegada(c, agora)
    return n


def _avisar_chegada(c, agora):
    from catalogo.models import Veiculo
    n = 0
    estoque = list(Veiculo.objects.filter(status__in=Veiculo.EM_LOJA, valor_venda__isnull=False))
    for p in Procura.objects.filter(avisada_em__isnull=True, lead__nao_contatar=False).select_related("lead"):
        termos = [t for t in utils.sem_acento(p.descricao).lower().split() if len(t) >= 3 and not t.isdigit()]
        for v in estoque:
            nome = utils.sem_acento(f"{v.marca_fmt} {v.modelo_fmt}").lower()
            if termos and all(t in nome for t in termos[:2]):
                _, criou = CampanhaEnvio.objects.get_or_create(campanha=c, lead=p.lead, passo=p.pk,
                                                               defaults={"enviar_em": agora, "extras": _dados_do_carro(v)})
                if criou:
                    p.avisada_em = agora
                    p.save(update_fields=["avisada_em"])
                    n += 1
                break
    return n


def registrar_procura(lead):
    """Lead foi para 'Não temos': guarda o que ele procurava para avisar quando chegar."""
    desc = (lead.carro_texto or "").split(" · ")[0].strip()
    if desc and not lead.procuras.filter(descricao=desc, avisada_em__isnull=True).exists():
        Procura.objects.create(lead=lead, descricao=desc[:150])


# ---------------------------------------------------------------- envio
def rodar_lote(agora=None, limite=LOTE):
    """Chamado pelo worker. Devolve quantas mensagens saíram neste ciclo."""
    agora = agora or timezone.now()
    if not whatsapp.configurado():
        return 0
    folga = settings.WHATSAPP_LIMITE_DIARIO - enviados_24h(agora)
    enviados = 0
    for c in Campanha.objects.filter(status=Campanha.Status.ENVIANDO).select_related("modelo"):
        pend = c.envios.filter(status="pendente").filter(enviar_em__isnull=True) | c.envios.filter(status="pendente", enviar_em__lte=agora)
        for e in pend.select_related("lead", "modelo").order_by("enviar_em", "id")[:limite * 4]:
            if e.lead.nao_contatar:                      # quem pediu para sair não gasta o limite
                e.status, e.erro = CampanhaEnvio.Status.FALHOU, "Pediu para não receber mensagens."
            elif enviados >= limite or folga <= 0:
                break
            else:
                try:
                    whatsapp.enviar_modelo(e.lead, e.modelo or c.modelo, extras=e.extras)
                    e.status, e.enviado_em = CampanhaEnvio.Status.ENVIADO, agora
                    enviados += 1
                    folga -= 1
                except whatsapp.WhatsAppErro as erro:
                    e.status, e.erro = CampanhaEnvio.Status.FALHOU, str(erro)[:300]
            e.save(update_fields=["status", "erro", "enviado_em"])
        if c.tipo in ESTATICAS and not c.envios.filter(status="pendente").exists():
            c.status = Campanha.Status.CONCLUIDA
            c.save(update_fields=["status"])
    return enviados
