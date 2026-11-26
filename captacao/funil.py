"""Funil personalizável: etapas editáveis (nome, cor, ordem, tag, palavras-chave, arquivo).

A lista fica em memória por alguns segundos (o CRM pergunta o nome da etapa a cada card); qualquer alteração limpa a lista."""
import time
import uuid

from django.db import transaction

from .models import EtapaFunil, Historico, Lead

PALETA = ["#22c55e", "#10b981", "#14b8a6", "#06b6d4", "#3b82f6", "#6366f1", "#8b5cf6", "#a855f7", "#ec4899", "#ef4444", "#f97316",
          "#f59e0b", "#eab308", "#64748b"]
SISTEMA = ("novo", "atendimento", "qualificado", "visita", "vendido", "perdido")

PADRAO = [
    # chave, nome, cor, tipo, lateral
    ("novo", "Novo", "#64748b", "normal", False),
    ("requalificar", "Requalificar", "#f97316", "normal", True),
    ("atendimento", "Em atendimento", "#3b82f6", "normal", False),
    ("sem_estoque", "Não temos", "#a855f7", "normal", True),
    ("qualificado", "Qualificado", "#8b5cf6", "normal", False),
    ("visita", "Visita agendada", "#6366f1", "normal", False),
    ("compareceu", "Compareceu", "#14b8a6", "normal", False),
    ("negociacao", "Negociação", "#f59e0b", "normal", False),
    ("vendido", "Vendido", "#22c55e", "ganho", False),
    ("perdido", "Perdido", "#ef4444", "perdido", False),
]

_mem = {"t": 0.0, "lista": None}


def invalidar():
    _mem["lista"] = None


def garantir_padroes():
    if not EtapaFunil.objects.exists():
        for i, (chave, nome, cor, tipo, lateral) in enumerate(PADRAO):
            EtapaFunil.objects.create(chave=chave, nome=nome, cor=cor, tipo=tipo, lateral=lateral, ordem=i * 10, sistema=chave in SISTEMA)
        invalidar()


def todas():
    if _mem["lista"] is None or time.time() - _mem["t"] > 3:
        garantir_padroes()
        _mem["lista"], _mem["t"] = list(EtapaFunil.objects.select_related("tag")), time.time()
    return _mem["lista"]


def mapa():
    return {e.chave: e for e in todas()}


def nome(chave):
    e = mapa().get(chave)
    return e.nome if e else (chave or "—")


def cor(chave):
    e = mapa().get(chave)
    return e.cor if e else "#64748b"


def choices():
    return [(e.chave, e.nome) for e in todas()]


def chaves():
    return [e.chave for e in todas()]


def abertas():
    return [e.chave for e in todas() if e.tipo == "normal"]


def fechadas():
    return [e.chave for e in todas() if e.tipo != "normal"]


def rank(chave):
    for i, e in enumerate(todas()):
        if e.chave == chave:
            return i
    return -1


def proxima(chave):
    """Próxima etapa do 'avançar': a seguinte que não é lateral nem 'perdido'. Laterais voltam para 'Em atendimento'."""
    todos = todas()
    atual = mapa().get(chave)
    if not atual or atual.tipo != "normal":
        return None
    if atual.lateral:
        return mapa().get("atendimento")
    seguintes = [e for e in todos if todos.index(e) > todos.index(atual) and not e.lateral and e.tipo != "perdido"]
    return seguintes[0] if seguintes else None


def com_form(chave):
    """Etapas que pedem dados antes de entrar (visita: dia/hora; ganho: valor e carro; perdido: motivo)."""
    e = mapa().get(chave)
    return bool(e and (e.tipo != "normal" or e.chave == "visita"))


# ---------------------------------------------------------------- edição
def _nova_chave():
    return "e" + uuid.uuid4().hex[:8]


@transaction.atomic
def criar(nome, cor="#3b82f6"):
    """Nova etapa: entra logo antes das de fechamento (vendido/perdido)."""
    garantir_padroes()
    ordem = min((e.ordem for e in todas() if e.tipo != "normal"), default=9990) - 5
    e = EtapaFunil.objects.create(chave=_nova_chave(), nome=nome[:40], cor=cor, ordem=ordem)
    reordenar([x.pk for x in EtapaFunil.objects.all()])
    return e


@transaction.atomic
def reordenar(ids):
    """ids na nova ordem (só as etapas informadas são reposicionadas, mantendo as de fechamento no fim)."""
    for i, pk in enumerate(ids):
        EtapaFunil.objects.filter(pk=pk).update(ordem=i * 10)
    invalidar()


@transaction.atomic
def apagar(etapa, destino_chave):
    """Apaga a etapa (menos as de sistema) levando os leads para a etapa de destino."""
    if etapa.sistema:
        raise ValueError("Esta etapa é usada pelo CRM e não pode ser apagada (você pode renomear e mudar a cor).")
    destino = EtapaFunil.objects.filter(chave=destino_chave).exclude(pk=etapa.pk).first()
    if not destino:
        raise ValueError("Escolha a etapa para onde os leads serão levados.")
    for l in Lead.objects.filter(etapa=etapa.chave):
        Historico.objects.create(lead=l, tipo=Historico.Tipo.ETAPA, de_etapa=etapa.chave, para_etapa=destino.chave,
                                 texto=f"Etapa “{etapa.nome}” foi apagada")
    Lead.objects.filter(etapa=etapa.chave).update(etapa=destino.chave)
    etapa.delete()
    invalidar()


def contagens():
    from django.db.models import Count
    c = dict(Lead.objects.values_list("etapa").annotate(n=Count("id")))
    return c
