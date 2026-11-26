"""Follow-up: sequência de tentativas para quem não respondeu.

Como funciona (parecido com o do Avise Aí, com melhorias):
- começa quando a equipe faz o 1º contato (abre o WhatsApp do lead) ou quando alguém inicia pelo CRM;
- cada passo tem espera, horário fixo opcional e até 3 variações de texto (A/B/C) com taxa de resposta medida;
- respeita o horário comercial (e domingo, se desligado): fora dele, empurra para a próxima manhã;
- PARA SOZINHO quando o cliente responde (pelo webhook do WhatsApp), quando o lead avança de etapa ou quando alguém marca;
- sem a API conectada, cada passo vira uma TAREFA do SDR (mensagem pronta + link do WhatsApp);
  com a API e o envio automático ligados, o CRM manda o texto (dentro das 24 h) ou o template aprovado (fora delas)."""
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import FollowUp, FollowUpConfig, FollowUpPasso, FollowUpTentativa, Historico, Lead

LETRAS = "ABC"
ETAPAS_QUE_ENCERRAM = (Lead.Etapa.QUALIFICADO, Lead.Etapa.VISITA, Lead.Etapa.COMPARECEU, Lead.Etapa.NEGOCIACAO,
                       Lead.Etapa.VENDIDO, Lead.Etapa.PERDIDO)

PASSOS_PADRAO = [
    dict(ordem=1, espera_dias=0, espera_horas=1, hora_envio=None, variantes=[
        "Olá, {nome}! Tudo bem? Aqui é da TopCar Veículos. Passando para saber se ficou alguma dúvida sobre {carro}. Posso ajudar com valores ou condições?",
        "Olá, {nome}, tudo bem? Falo da TopCar Veículos. Você demonstrou interesse em {carro}. Ainda faz sentido para você? Fico à disposição.",
        "Olá, {nome}! Sei que a rotina é corrida, então serei breve: continuo à disposição para ajudar com {carro}. Quer retomar de onde paramos?"]),
    dict(ordem=2, espera_dias=1, espera_horas=0, hora_envio=10, variantes=[
        "Olá, {nome}! Conseguiu ver minha mensagem anterior? Posso enviar mais fotos e as condições de pagamento de {carro}.",
        "{nome}, passando rapidamente: se quiser, simulo a parcela de {carro} com a entrada que você preferir.",
        "Olá, {nome}! Se ainda estiver pensando em {carro}, posso te mostrar também outras opções parecidas. Quer ver?"]),
    dict(ordem=3, espera_dias=2, espera_horas=0, hora_envio=10, variantes=[
        "Olá, {nome}! Surgiram algumas opções interessantes aqui na loja. Você ainda está procurando um carro?",
        "{nome}, tudo bem? Queria saber se você ainda tem interesse em {carro}. Se sim, passo as condições atuais.",
        "Olá, {nome}, uma pergunta rápida: você ainda busca um carro ou já resolveu? Assim sei como ajudar melhor."]),
    dict(ordem=4, espera_dias=3, espera_horas=0, hora_envio=10, variantes=[
        "Olá, {nome}! Como não tivemos retorno, vou encerrar o atendimento por agora para não incomodar. Quando quiser retomar, é só me chamar por aqui.",
        "Olá, {nome}, vou pausar nosso atendimento por aqui. Se ainda fizer sentido, responda esta mensagem que continuamos de onde paramos.",
        "{nome}, não quero ser insistente, então este é meu último contato por ora. Se precisar, estou à disposição."]),
]


def garantir_passos():
    """Cria a sequência padrão na primeira vez (a tela de configuração edita depois)."""
    if not FollowUpPasso.objects.exists():
        for p in PASSOS_PADRAO:
            FollowUpPasso.objects.create(**p)


def passos_ativos():
    garantir_passos()
    return list(FollowUpPasso.objects.filter(ativo=True).order_by("ordem"))


# ---------------------------------------------------------------- horário
def ajustar_horario(dt, cfg):
    """Empurra `dt` para dentro da janela de envio (hora_inicio..hora_fim, sem domingo se desligado)."""
    loc = timezone.localtime(dt)
    for _ in range(10):
        if not cfg.enviar_domingo and loc.weekday() == 6:
            loc = datetime.combine(loc.date() + timedelta(days=1), time(cfg.hora_inicio), tzinfo=loc.tzinfo)
        elif loc.hour < cfg.hora_inicio:
            loc = loc.replace(hour=cfg.hora_inicio, minute=0, second=0, microsecond=0)
        elif loc.hour >= cfg.hora_fim:
            loc = datetime.combine(loc.date() + timedelta(days=1), time(cfg.hora_inicio), tzinfo=loc.tzinfo)
        else:
            break
    return loc


def agendar(passo, base, cfg):
    """Quando o passo vence, contando a partir de `base`."""
    loc = timezone.localtime(base)
    loc = loc + (timedelta(days=passo.espera_dias) if passo.espera_dias else timedelta(hours=passo.espera_horas))
    if passo.hora_envio is not None and passo.espera_dias:
        loc = loc.replace(hour=passo.hora_envio, minute=0, second=0, microsecond=0)
    return ajustar_horario(loc, cfg)


# ---------------------------------------------------------------- ciclo de vida
def ativo_do_lead(lead):
    return lead.followups.filter(status__in=[FollowUp.Status.ATIVO, FollowUp.Status.PAUSADO]).first()


@transaction.atomic
def iniciar(lead, agora=None):
    """Inicia a sequência (ignora se já há uma em andamento, se o lead já fechou ou se está desligado)."""
    agora = agora or timezone.now()
    cfg = FollowUpConfig.obter()
    if not cfg.ativo or lead.nao_contatar or lead.etapa in ETAPAS_QUE_ENCERRAM or ativo_do_lead(lead):
        return None
    passos = passos_ativos()
    if not passos:
        return None
    fu = FollowUp.objects.create(lead=lead, passo_atual=passos[0].ordem, variante=lead.pk % len(LETRAS),
                                 proximo_em=agendar(passos[0], agora, cfg), iniciado_em=agora)
    Historico.objects.create(lead=lead, tipo=Historico.Tipo.NOTA, criado_em=agora,
                             texto=f"Follow-up iniciado ({len(passos)} tentativas, 1ª em {timezone.localtime(fu.proximo_em):%d/%m %H:%M})")
    return fu


def _fechar(fu, status, agora, texto, usuario=None):
    fu.status, fu.finalizado_em, fu.proximo_em = status, agora, None
    fu.save(update_fields=["status", "finalizado_em", "proximo_em"])
    Historico.objects.create(lead=fu.lead, tipo=Historico.Tipo.NOTA, criado_em=agora, usuario=usuario, texto=texto[:500])


def encerrar_se_avancou(lead, agora=None):
    """Chamado ao mudar de etapa: se o lead já está qualificado/fechado, o follow-up não faz mais sentido."""
    if lead.etapa in ETAPAS_QUE_ENCERRAM:
        agora = agora or timezone.now()
        for fu in lead.followups.filter(status__in=[FollowUp.Status.ATIVO, FollowUp.Status.PAUSADO]):
            _fechar(fu, FollowUp.Status.ENCERRADO, agora, f"Follow-up encerrado: lead foi para {lead.get_etapa_display()}")


def cliente_respondeu(lead, usuario=None, agora=None):
    """O cliente respondeu (webhook do WhatsApp ou marcação manual): para a sequência e credita a variação."""
    agora = agora or timezone.now()
    n = 0
    for fu in lead.followups.filter(status__in=[FollowUp.Status.ATIVO, FollowUp.Status.PAUSADO]):
        ultima = fu.tentativas.order_by("-feita_em").first()
        if ultima:
            ultima.respondeu = True
            ultima.save(update_fields=["respondeu"])
        _fechar(fu, FollowUp.Status.RESPONDEU, agora, "Cliente respondeu: follow-up parado", usuario)
        n += 1
    return n


def pausar(fu, usuario=None, agora=None):
    if fu.status == FollowUp.Status.ATIVO:
        fu.status = FollowUp.Status.PAUSADO
        fu.save(update_fields=["status"])
        Historico.objects.create(lead=fu.lead, tipo=Historico.Tipo.NOTA, usuario=usuario, texto="Follow-up pausado")


def retomar(fu, usuario=None, agora=None):
    if fu.status == FollowUp.Status.PAUSADO:
        agora = agora or timezone.now()
        fu.status, fu.proximo_em = FollowUp.Status.ATIVO, ajustar_horario(agora, FollowUpConfig.obter())
        fu.save(update_fields=["status", "proximo_em"])
        Historico.objects.create(lead=fu.lead, tipo=Historico.Tipo.NOTA, usuario=usuario, texto="Follow-up retomado")


# ---------------------------------------------------------------- mensagem
def passo_do(fu):
    return FollowUpPasso.objects.filter(ordem=fu.passo_atual).first()


def montar_mensagem(fu, passo=None, usuario=None):
    passo = passo or passo_do(fu)
    if not passo or not passo.variantes:
        return ""
    texto = passo.variantes[fu.variante % len(passo.variantes)]
    nome = (fu.lead.nome or "").strip()
    if FollowUpConfig.obter().primeiro_nome and nome:
        nome = nome.split()[0]
    carro = (fu.lead.carro_texto or "").split(" · ")[0] or "o carro que você viu"
    return texto.replace("{nome}", nome.title() if nome else "tudo bem").replace("{carro}", carro)


def total_passos():
    return len(passos_ativos())


def posicao(fu):
    """(n, total): qual tentativa é a próxima, entre as ativas."""
    ordens = [p.ordem for p in passos_ativos()]
    return (ordens.index(fu.passo_atual) + 1 if fu.passo_atual in ordens else len(ordens)), len(ordens)


@transaction.atomic
def registrar_tentativa(fu, usuario=None, agora=None, via_api=False, mensagem=None):
    """Marca o passo como feito (a equipe enviou, ou a API enviou) e agenda o próximo (ou esgota a sequência)."""
    agora = agora or timezone.now()
    passo = passo_do(fu)
    if fu.status != FollowUp.Status.ATIVO or not passo:
        return None
    texto = mensagem if mensagem is not None else montar_mensagem(fu, passo, usuario)
    t = FollowUpTentativa.objects.create(followup=fu, passo=passo.ordem, variante=fu.variante, mensagem=texto,
                                         via_api=via_api, usuario=usuario, feita_em=agora)
    n, total = posicao(fu)
    Historico.objects.create(lead=fu.lead, tipo=Historico.Tipo.NOTA, usuario=usuario, criado_em=agora,
                             texto=f"Follow-up {n}/{total} enviado{' (automático)' if via_api else ''}: {texto}"[:500])
    seguinte = next((p for p in passos_ativos() if p.ordem > passo.ordem), None)
    if seguinte:
        fu.passo_atual, fu.proximo_em = seguinte.ordem, agendar(seguinte, agora, FollowUpConfig.obter())
        fu.save(update_fields=["passo_atual", "proximo_em"])
    else:
        _fechar(fu, FollowUp.Status.ESGOTADO, agora, "Follow-up sem resposta: todas as tentativas foram enviadas")
    return t


def pular(fu, usuario=None, agora=None):
    """Pula o passo atual sem enviar (ex.: o cliente já foi atendido por telefone)."""
    agora = agora or timezone.now()
    passo = passo_do(fu)
    seguinte = next((p for p in passos_ativos() if passo and p.ordem > passo.ordem), None)
    if seguinte:
        fu.passo_atual, fu.proximo_em = seguinte.ordem, agendar(seguinte, agora, FollowUpConfig.obter())
        fu.save(update_fields=["passo_atual", "proximo_em"])
    else:
        _fechar(fu, FollowUp.Status.ESGOTADO, agora, "Follow-up encerrado: último passo pulado", usuario)


# ---------------------------------------------------------------- fila e números
PESO_CLASSE = {"A": 0, "B": 1, "C": 2}


def devidos(agora=None):
    """Tarefas para fazer agora: mais quentes primeiro, depois as mais atrasadas."""
    agora = agora or timezone.now()
    lista = list(FollowUp.objects.select_related("lead", "lead__veiculo", "lead__vendedor")
                 .filter(status=FollowUp.Status.ATIVO, proximo_em__lte=agora))
    lista.sort(key=lambda f: (PESO_CLASSE.get(f.lead.classe, 1), f.proximo_em))
    return lista


def contagens(agora=None):
    agora = agora or timezone.now()
    q = FollowUp.objects
    return {
        "agora": q.filter(status="ativo", proximo_em__lte=agora).count(),
        "agendados": q.filter(status="ativo", proximo_em__gt=agora).count(),
        "pausados": q.filter(status="pausado").count(),
        "esgotados": q.filter(status="esgotado").count(),
        "responderam": q.filter(status="respondeu").count(),
    }


def desempenho():
    """Taxa de resposta por passo e variação (A/B/C): qual texto funciona melhor."""
    linhas = (FollowUpTentativa.objects.values("passo", "variante")
              .annotate(enviadas=Count("id"), respostas=Count("id", filter=Q(respondeu=True))).order_by("passo", "variante"))
    return [{"passo": l["passo"], "letra": LETRAS[l["variante"] % 3], "enviadas": l["enviadas"], "respostas": l["respostas"],
             "taxa": round(100 * l["respostas"] / l["enviadas"]) if l["enviadas"] else 0} for l in linhas]
