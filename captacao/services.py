"""Regras de negócio: captura (com deduplicação), mudança de etapa e LGPD."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import meta, telegram, utils
from .models import EventoMeta, Historico, Interacao, Lead

JANELA_DEDUP_DIAS = 30


class RegraErro(ValueError):
    """Violação de regra do funil (mostrada ao usuário)."""


def resumo_respostas(r):
    troca = "não" if r.get("troca") != "sim" else f"sim ({r.get('troca_carro', '')})".replace(" ()", "")
    extra = " · Quer agendar visita" if r.get("intencao") == "visita" else ""
    return (f"Pagamento: {utils.rotulo(utils.PAGAMENTO, r.get('pagamento'))} · "
            f"Entrada: {utils.rotulo(utils.ENTRADA, r.get('entrada'))} · Troca: {troca} · "
            f"Compra: {utils.rotulo(utils.PRAZO, r.get('prazo'))}{extra}")


@transaction.atomic
def capturar_lead(veiculo, nome, telefone, respostas, tracking, agora=None):
    """Cria o lead ou, se o telefone já existe nos últimos 30 dias, registra nova interação.
    `telefone` já normalizado (55 + DDD + número). Devolve (lead, criado)."""
    agora = agora or timezone.now()
    classe = utils.classificar(respostas)
    carro = f"{veiculo.marca_fmt} {veiculo.modelo_fmt} {veiculo.ano} · {utils.preco_brl(veiculo.valor_venda)}"
    campos = {k: tracking.get(k, "") for k in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                                                "fbclid", "fbp", "fbc", "user_agent", "url_origem")}
    campos["user_agent"] = campos["user_agent"][:500]
    campos["url_origem"] = campos["url_origem"][:1000]
    ip = tracking.get("ip") or None
    event_id = tracking.get("event_id", "")

    existente = (Lead.objects.select_for_update()
                 .filter(telefone=telefone, criado_em__gte=agora - timedelta(days=JANELA_DEDUP_DIAS))
                 .order_by("-criado_em").first())
    if existente:
        Interacao.objects.create(lead=existente, veiculo=veiculo, respostas=respostas,
                                 utm_content=campos["utm_content"], criado_em=agora)
        existente.veiculo, existente.carro_texto, existente.respostas = veiculo, carro, respostas
        # a classe só sobe (ou muda se o cliente refinou a resposta); tracking novo só preenche o que faltava
        existente.classe = classe
        for k, v in campos.items():
            if v and not getattr(existente, k):
                setattr(existente, k, v)
        existente.event_id_lead = event_id or existente.event_id_lead
        existente.save()
        Historico.objects.create(lead=existente, tipo=Historico.Tipo.FORM, criado_em=agora,
                                 texto=f"Voltou a pedir: {carro}. {resumo_respostas(respostas)}"[:500])
        return existente, False

    lead = Lead.objects.create(
        nome=nome[:120], telefone=telefone, veiculo=veiculo, carro_texto=carro, origem=Lead.Origem.META_LP,
        classe=classe, respostas=respostas, ip=ip, event_id_lead=event_id,
        consentimento_em=agora, consentimento_ip=ip, criado_em=agora, etapa_desde=agora, **campos)
    Historico.objects.create(lead=lead, tipo=Historico.Tipo.CRIADO, criado_em=agora,
                             texto=f"Capturado pela página: {carro}. {resumo_respostas(respostas)}"[:500])
    try:
        telegram.avisar_novo_lead(lead)          # só entra na fila; quem envia é o worker
    except Exception:  # noqa: BLE001 - aviso nunca pode impedir a captura do lead
        pass
    return lead, True


def criar_lead_manual(nome, telefone, veiculo=None, origem=Lead.Origem.WHATSAPP, usuario=None, observacao=""):
    """Cadastro rápido (WhatsApp direto, portais, loja). Não dispara eventos de site (sem fbp/fbc)."""
    carro = f"{veiculo.marca_fmt} {veiculo.modelo_fmt} {veiculo.ano} · {utils.preco_brl(veiculo.valor_venda)}" if veiculo else ""
    lead = Lead.objects.create(nome=nome[:120], telefone=telefone, veiculo=veiculo, carro_texto=carro, origem=origem,
                               vendedor=usuario, observacao=observacao)
    Historico.objects.create(lead=lead, tipo=Historico.Tipo.CRIADO, usuario=usuario, texto="Cadastro manual")
    return lead


@transaction.atomic
def mudar_etapa(lead, nova, usuario=None, motivo_perda="", veiculo_vendido=None, valor_venda=None, visita_em=None):
    """Única porta para trocar a etapa. Grava histórico e enfileira o evento da Meta (uma vez por lead)."""
    if nova not in Lead.Etapa.values:
        raise RegraErro("Etapa inválida.")
    if nova == lead.etapa:
        return False
    if nova == Lead.Etapa.PERDIDO and motivo_perda not in Lead.MotivoPerda.values:
        raise RegraErro("Informe o motivo da perda.")
    if nova == Lead.Etapa.VENDIDO:
        if valor_venda is None or Decimal(valor_venda) <= 0:
            raise RegraErro("Informe o valor da venda.")
        if veiculo_vendido is None:
            raise RegraErro("Informe o veículo vendido.")

    agora = timezone.now()
    antiga = lead.etapa
    Historico.objects.create(lead=lead, tipo=Historico.Tipo.ETAPA, usuario=usuario, de_etapa=antiga, para_etapa=nova,
                             duracao_etapa=agora - lead.etapa_desde,
                             texto=(f"Motivo: {Lead.MotivoPerda(motivo_perda).label}" if nova == Lead.Etapa.PERDIDO else "")[:500])
    if antiga == Lead.Etapa.NOVO and not lead.primeira_resposta_em:
        lead.primeira_resposta_em = agora
    if lead.vendedor_id is None and usuario is not None:
        lead.vendedor = usuario
    lead.etapa, lead.etapa_desde = nova, agora
    lead.motivo_perda = motivo_perda if nova == Lead.Etapa.PERDIDO else ""
    if nova == Lead.Etapa.VISITA and visita_em:
        lead.visita_em = visita_em
    if nova == Lead.Etapa.VENDIDO:
        lead.veiculo_vendido, lead.valor_venda, lead.vendido_em = veiculo_vendido, Decimal(valor_venda), agora
    elif antiga == Lead.Etapa.VENDIDO:
        lead.vendido_em = None
    lead.save()
    meta.enfileirar_evento_crm(lead, nova)
    return True


def marcar_atendimento(lead, usuario=None):
    """Chamado ao abrir o WhatsApp: tira o lead de 'Novo' e marca a primeira resposta."""
    if lead.etapa == Lead.Etapa.NOVO:
        return mudar_etapa(lead, Lead.Etapa.ATENDIMENTO, usuario)
    return False


def adicionar_nota(lead, texto, usuario=None):
    if texto.strip():
        Historico.objects.create(lead=lead, tipo=Historico.Tipo.NOTA, usuario=usuario, texto=texto.strip()[:500])


def trocar_consultor(lead, novo, usuario=None):
    if (novo.pk if novo else None) == lead.vendedor_id:
        return
    lead.vendedor = novo
    lead.save(update_fields=["vendedor", "atualizado_em"])
    Historico.objects.create(lead=lead, tipo=Historico.Tipo.CONSULTOR, usuario=usuario,
                             texto=f"Consultor: {novo.get_username() if novo else 'sem consultor'}")


@transaction.atomic
def excluir_dados_do_lead(lead):
    """LGPD: apaga o lead e tudo ligado a ele (interações, histórico) e anonimiza os eventos já enviados."""
    EventoMeta.objects.filter(lead=lead, status=EventoMeta.Status.PENDENTE).delete()   # nada mais sai com dados dele
    EventoMeta.objects.filter(lead=lead).update(lead=None, payload={}, resposta="")
    lead.delete()
