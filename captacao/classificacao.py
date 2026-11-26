"""Etiquetas e classificação automática do funil (como no Avise Aí).

O lead recebe etiquetas (à mão ou pelas regras) e o funil anda sozinho conforme o que o cliente escreve:
- o cliente respondeu e o lead ainda era "Novo"  ->  "Em atendimento";
- palavras-chave (editáveis em Funil e etiquetas): põem etiqueta e, se a regra mandar, levam o lead para uma etapa.
Regras de segurança: a etapa SÓ AVANÇA (nunca volta sozinha), não mexe em quem já foi vendido, e levar para "Perdido"
exige motivo na regra. Tudo que muda sozinho fica no histórico do lead."""
import re

from django.db import transaction

from . import campanhas, funil, services, utils
from .models import Etiqueta, Historico, Lead, RegraClassificacao


ETIQUETAS_PADRAO = [
    ("Interessado", "#2ee66b", False), ("Financiamento", "#5ab0ff", False), ("Troca", "#f5b942", False),
    ("Quer visitar", "#a78bfa", False), ("Quente", "#ef5350", False), ("OLX", "#7c3aed", False), ("Napista", "#fb923c", False),
    ("Mobiauto", "#14b8a6", False), ("Instagram", "#ec4899", False), ("Facebook", "#3b82f6", False), ("Anúncio pago", "#22c55e", False),
    ("Site", "#94a3b8", False), ("Não contatar", "#6b7280", True),
]

REGRAS_PADRAO = [
    ("Pediu preço ou condições", "preço, preco, valor, quanto, quanto é, quanto custa, parcela, simular, simulação, ainda tem, está disponível, tem disponível",
     "Interessado", "", ""),
    ("Financiamento", "financiar, financiamento, financiado, entrada, cpf, data de nascimento, cnh", "Financiamento", "", ""),
    ("Dados para análise de crédito", "possui cnh, data de nascimento, meu cpf", "Financiamento", Lead.Etapa.QUALIFICADO, ""),
    ("Troca", "troca, trocar, meu carro, minha moto, na troca", "Troca", "", ""),
    ("Quer visitar", "visita, passar aí, passar ai, ir aí, ir ai, onde fica, endereço, endereco, horário, horario, posso ir, vou passar",
     "Quer visitar", "", ""),
    ("Já comprou outro", "comprei outro, já comprei, ja comprei, acabei de comprar, comprei meu carro", "", Lead.Etapa.PERDIDO, Lead.MotivoPerda.COMPROU_OUTRO),
    ("Desistiu", "não tenho mais interesse, nao tenho mais interesse, não quero mais, nao quero mais, desisti, sem interesse",
     "", Lead.Etapa.PERDIDO, Lead.MotivoPerda.SO_PESQUISANDO),
]


def garantir_padroes():
    """Cria as etiquetas e regras iniciais na primeira vez (tudo editável na tela Funil e etiquetas)."""
    if not Etiqueta.objects.exists():
        for i, (nome, cor, bloq) in enumerate(ETIQUETAS_PADRAO):
            Etiqueta.objects.create(nome=nome, cor=cor, bloqueia_followup=bloq, ordem=i)
    if not RegraClassificacao.objects.exists():
        for i, (nome, palavras, etiqueta, etapa, motivo) in enumerate(REGRAS_PADRAO):
            RegraClassificacao.objects.create(nome=nome, palavras=palavras, ordem=i, etapa=etapa, motivo_perda=motivo,
                                              etiqueta=Etiqueta.objects.filter(nome=etiqueta).first() if etiqueta else None)


def etiqueta(nome):
    garantir_padroes()
    return Etiqueta.objects.filter(nome=nome).first()


def etiquetar(lead, nome, texto_historico=""):
    """Põe a etiqueta (pelo nome) no lead se ele ainda não tiver. Devolve True se pôs agora."""
    e = etiqueta(nome)
    if not e or lead.etiquetas.filter(pk=e.pk).exists():
        return False
    lead.etiquetas.add(e)
    etiqueta_adicionada(lead, e)
    if texto_historico:
        Historico.objects.create(lead=lead, tipo=Historico.Tipo.NOTA, texto=texto_historico[:500])
    return True


def etiquetar_origem(lead):
    """Etiqueta de onde veio: portal, anúncio pago ou site."""
    if lead.portal:
        etiquetar(lead, lead.get_portal_display())
    elif lead.origem == Lead.Origem.META_LP:
        etiquetar(lead, "Anúncio pago")


def _norm(t):
    return re.sub(r"\s+", " ", utils.sem_acento(t or "").lower()).strip()


def _palavras(regra):
    return [p for p in (_norm(x) for x in re.split(r"[,\n]", regra.palavras)) if p]


def _casa(texto, palavra):
    return re.search(rf"(?<!\w){re.escape(palavra)}(?!\w)", texto) is not None


def pode_avancar(lead, destino):
    """A etapa só avança (pela ordem do funil). 'Perdido' pode vir de qualquer etapa aberta; nada mexe em quem já foi vendido."""
    m = funil.mapa()
    atual, alvo = m.get(lead.etapa), m.get(destino)
    if not atual or not alvo or atual.tipo != "normal":
        return False
    if alvo.tipo == "perdido":
        return True
    return alvo.tipo == "normal" and funil.rank(destino) > funil.rank(lead.etapa)


def _grupo_casa(grupo, texto):
    palavras = [p for p in (_norm(x) for x in grupo.get("palavras", [])) if p]
    if not palavras:
        return False
    achou = [_casa(texto, p) for p in palavras]
    return all(achou) if grupo.get("todas") else any(achou)


def etapa_por_palavras(texto):
    """Etapa cujos grupos de palavras-chave casam com o texto (a mais avançada do funil, se houver mais de uma)."""
    t = _norm(texto)
    if not t:
        return None
    casam = [e for e in funil.todas() if any(_grupo_casa(g, t) for g in (e.grupos or []))]
    return max(casam, key=lambda e: funil.rank(e.chave)) if casam else None


def testar(texto):
    """'Teste rápido' da tela do funil: onde a mensagem cairia hoje (sem mexer em nenhum lead)."""
    garantir_padroes()
    t = _norm(texto)
    regras = [r for r in RegraClassificacao.objects.filter(ativa=True).select_related("etiqueta") if any(_casa(t, p) for p in _palavras(r))]
    return {"etapa": etapa_por_palavras(texto), "regras": regras, "etiquetas": [r.etiqueta for r in regras if r.etiqueta_id]}


def etiqueta_adicionada(lead, etiqueta):
    """Toda vez que uma etiqueta é posta no lead (à mão ou por regra): entra nas réguas e, se a etapa tem essa etiqueta
    como gatilho, o lead é movido para ela (como no Avise Aí: 'Definir por tag')."""
    campanhas.ao_etiquetar(lead, etiqueta)
    alvo = next((e for e in funil.todas() if e.tag_id == etiqueta.pk), None)
    if alvo and alvo.chave != lead.etapa and lead.etapa not in funil.fechadas():
        try:
            if funil.com_form(alvo.chave):
                return
            services.mudar_etapa(lead, alvo.chave, None)
            Historico.objects.create(lead=lead, tipo=Historico.Tipo.NOTA, texto=f"Funil automático: etiqueta “{etiqueta.nome}”")
        except services.RegraErro:
            pass


def arquivo_recebido(lead):
    """O cliente mandou um arquivo/foto: leva para a etapa marcada com 'definir por arquivo' (só avança)."""
    alvo = next((e for e in funil.todas() if e.por_arquivo), None)
    if alvo and pode_avancar(lead, alvo.chave) and not funil.com_form(alvo.chave):
        services.mudar_etapa(lead, alvo.chave, None)
        Historico.objects.create(lead=lead, tipo=Historico.Tipo.NOTA, texto="Funil automático: o cliente enviou um arquivo")


@transaction.atomic
def aplicar_regras(lead, texto):
    """Aplica as regras a uma mensagem do cliente. Devolve a lista de nomes das regras que dispararam."""
    garantir_padroes()
    t = _norm(texto)
    if not t:
        return []
    dispararam = []
    for regra in RegraClassificacao.objects.filter(ativa=True).select_related("etiqueta"):
        if not any(_casa(t, p) for p in _palavras(regra)):
            continue
        dispararam.append(regra.nome)
        if regra.etiqueta_id and not lead.etiquetas.filter(pk=regra.etiqueta_id).exists():
            lead.etiquetas.add(regra.etiqueta)
            etiqueta_adicionada(lead, regra.etiqueta)
        if regra.etapa and pode_avancar(lead, regra.etapa):
            try:
                services.mudar_etapa(lead, regra.etapa, None, motivo_perda=regra.motivo_perda)
                Historico.objects.create(lead=lead, tipo=Historico.Tipo.NOTA, texto=f"Funil automático: regra “{regra.nome}”"[:500])
            except services.RegraErro:
                pass
    alvo = etapa_por_palavras(texto)
    if alvo and pode_avancar(lead, alvo.chave) and not funil.com_form(alvo.chave):
        services.mudar_etapa(lead, alvo.chave, None)
        Historico.objects.create(lead=lead, tipo=Historico.Tipo.NOTA, texto=f"Funil automático: palavras da etapa “{alvo.nome}”")
        dispararam.append(f"etapa {alvo.nome}")
    return dispararam


def cliente_respondeu(lead):
    """O cliente escreveu: sai de 'Novo' para 'Em atendimento' (ele já está conversando com a loja)."""
    if lead.etapa in (Lead.Etapa.NOVO, Lead.Etapa.REQUALIFICAR):
        services.mudar_etapa(lead, Lead.Etapa.ATENDIMENTO, None)
        Historico.objects.create(lead=lead, tipo=Historico.Tipo.NOTA, texto="Funil automático: o cliente respondeu")
        return True
    return False
