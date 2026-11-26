"""Mensagens do agendamento: confirmação ao marcar, lembretes (1 dia e 2 horas antes) e agradecimento depois da visita.

- Dentro da janela de 24 h vai o texto configurado; fora dela, o modelo aprovado escolhido na configuração.
- Sem API conectada (ou sem janela e sem modelo), a mensagem fica "programada"/"não enviada" e a agenda mostra o botão
  para enviar pelo WhatsApp do celular com o texto já pronto.
- Cancelou, faltou ou o cliente pediu para não receber: nada sai."""
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

from . import modelos, whatsapp
from .models import AgendaConfig, Agendamento, AgendamentoMensagem as M

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def extras(ag):
    ini = timezone.localtime(ag.inicio)
    hoje = timezone.localdate()
    quando = "hoje" if ini.date() == hoje else "amanhã" if ini.date() == hoje + timedelta(days=1) else f"no dia {ini:%d/%m}"
    return {"data": f"{ini:%d/%m}", "hora": f"{ini:%H:%M}", "dia_semana": DIAS[ini.weekday()], "quando": quando,
            "atividade": ag.atividade.nome if ag.atividade else "Visita à loja", "profissional": ag.profissional.nome if ag.profissional else "nossa equipe"}


def texto(ag, tipo):
    cfg = AgendaConfig.obter()
    modelo_txt = {M.Tipo.CONFIRMACAO: cfg.txt_confirmacao, M.Tipo.POS: cfg.txt_pos}.get(tipo, cfg.txt_lembrete)
    e = extras(ag)
    primeiro = ((ag.lead.nome or "").split() or [""])[0].title() or "tudo bem"
    valores = {"nome": primeiro, "data": e["data"], "hora": e["hora"], "dia": e["dia_semana"], "quando": e["quando"], "atividade": e["atividade"],
               "profissional": e["profissional"], "endereco": settings.LOJA.get("endereco", ""), "carro": (ag.lead.carro_texto or "").split(" · ")[0]}
    for k, v in valores.items():
        modelo_txt = modelo_txt.replace("{%s}" % k, v)
    return modelo_txt


def link_manual(ag, tipo):
    return f"https://wa.me/{ag.lead.telefone}?text={quote(texto(ag, tipo))}"


def programar(ag, agora=None):
    """Cria as mensagens do agendamento conforme a configuração (só as que ainda estão no futuro, exceto a confirmação)."""
    agora = agora or timezone.now()
    cfg = AgendaConfig.obter()
    planos = []
    if cfg.confirmar:
        planos.append((M.Tipo.CONFIRMACAO, agora))
    if cfg.lembrete_24h and ag.inicio - timedelta(hours=24) > agora:
        planos.append((M.Tipo.LEMBRETE_24H, ag.inicio - timedelta(hours=24)))
    if cfg.lembrete_2h and ag.inicio - timedelta(hours=2) > agora:
        planos.append((M.Tipo.LEMBRETE_2H, ag.inicio - timedelta(hours=2)))
    if cfg.pos_visita:
        planos.append((M.Tipo.POS, ag.fim + timedelta(hours=2)))
    for tipo, quando in planos:
        M.objects.get_or_create(agendamento=ag, tipo=tipo, defaults={"enviar_em": quando})


def cancelar_pendentes(ag, motivo="Agendamento cancelado."):
    ag.mensagens.filter(status="pendente").update(status="cancelada", erro=motivo)


def _modelo(cfg, tipo):
    return {M.Tipo.CONFIRMACAO: cfg.modelo_confirmacao, M.Tipo.POS: cfg.modelo_pos}.get(tipo, cfg.modelo_lembrete)


def enviar(m, agora=None):
    """Tenta enviar uma mensagem. Devolve True se saiu pela API."""
    agora = agora or timezone.now()
    ag, cfg = m.agendamento, AgendaConfig.obter()
    if ag.lead.nao_contatar:
        m.status, m.erro = "cancelada", "Cliente pediu para não receber mensagens."
    elif not whatsapp.configurado():
        m.status, m.erro = "pendente", "API do WhatsApp ainda não conectada."
        m.save(update_fields=["status", "erro"])
        return False
    else:
        try:
            if whatsapp.janela_aberta(ag.lead.telefone, agora):
                whatsapp.enviar_texto(ag.lead, texto(ag, m.tipo))
            else:
                mod = _modelo(cfg, m.tipo)
                if not (mod and mod.aprovado):
                    raise whatsapp.WhatsAppErro("Fora da janela de 24 h e sem modelo aprovado configurado para esta mensagem.")
                whatsapp.enviar_modelo(ag.lead, mod, extras=extras(ag))
            m.status, m.enviada_em, m.erro = "enviada", agora, ""
        except whatsapp.WhatsAppErro as e:
            m.status, m.erro = "falhou", str(e)[:300]
    m.save(update_fields=["status", "erro", "enviada_em"])
    return m.status == "enviada"


def rodar(agora=None, limite=30):
    """Worker: envia as mensagens vencidas dos agendamentos ainda válidos."""
    agora = agora or timezone.now()
    if not whatsapp.configurado():
        return 0
    n = 0
    for m in M.objects.filter(status="pendente", enviar_em__lte=agora).select_related("agendamento", "agendamento__lead")[:limite]:
        st = m.agendamento.status
        valido = (st in ("agendado", "confirmado", "realizado")) if m.tipo == M.Tipo.POS else st in ("agendado", "confirmado")
        if m.tipo == M.Tipo.POS and st != "realizado" and agora < m.agendamento.fim + timedelta(hours=12):
            continue                                   # ainda esperando a equipe marcar "realizado"
        if not valido:
            m.status, m.erro = "cancelada", f"Agendamento {m.agendamento.get_status_display().lower()}."
            m.save(update_fields=["status", "erro"])
            continue
        n += enviar(m, agora)
    return n
