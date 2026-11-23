"""Funções puras: telefone, hash, fbc, classe do lead, slug e mensagem do WhatsApp."""
import hashlib
import re
import time
import unicodedata
from decimal import Decimal
from urllib.parse import quote


def so_digitos(texto):
    return re.sub(r"\D", "", texto or "")


def normalizar_telefone(texto):
    """Devolve só dígitos com 55 (ex.: 5581999998888) ou '' se não for celular/fixo BR válido.
    Exige DDD (11-99) e, para 11 dígitos, o 9 na frente."""
    d = so_digitos(texto)
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) not in (10, 11):
        return ""
    ddd, numero = d[:2], d[2:]
    if not (11 <= int(ddd) <= 99):
        return ""
    if len(d) == 11 and numero[0] != "9":
        return ""
    if len(d) == 10 and numero[0] not in "2345":   # fixo
        return ""
    return "55" + d


def sha256(valor):
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


def sem_acento(texto):
    return "".join(c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c))


def normalizar_nome(texto):
    """Minúsculas, sem acento e sem pontuação (padrão de hash da Meta para fn/ln)."""
    return " ".join(re.sub(r"[^a-z0-9 ]", "", sem_acento(texto).lower()).split())


def hash_telefone(telefone):
    d = normalizar_telefone(telefone) or so_digitos(telefone)
    return sha256(d) if d else ""


def hash_nome(nome):
    n = normalizar_nome(nome)
    return sha256(n) if n else ""


def separar_nome(nome):
    partes = normalizar_nome(nome).split()
    if not partes:
        return "", ""
    return partes[0], (partes[-1] if len(partes) > 1 else "")


def montar_fbc(fbc_cookie="", fbclid="", agora_ms=None):
    """Usa o cookie _fbc; sem ele, e havendo fbclid, monta fb.1.<timestamp_ms>.<fbclid>."""
    if fbc_cookie:
        return fbc_cookie
    if fbclid:
        return f"fb.1.{agora_ms if agora_ms is not None else int(time.time() * 1000)}.{fbclid}"
    return ""


# ---- formulário -------------------------------------------------------------------------------
PAGAMENTO = [("a_vista", "À vista"), ("financiado", "Financiado"), ("consorcio", "Consórcio contemplado")]
ENTRADA = [("sem", "Sem entrada"), ("ate_10", "Até R$ 10 mil"), ("10_30", "R$ 10–30 mil"), ("acima_30", "Acima de R$ 30 mil")]
TROCA = [("nao", "Não"), ("sim", "Sim")]
INTENCAO = [("simular", "Simular"), ("visita", "Agendar visita")]
PRAZO = [("semana", "Esta semana"), ("30_dias", "Em até 30 dias"), ("pesquisando", "Só pesquisando")]


def rotulo(opcoes, valor):
    return dict(opcoes).get(valor, valor or "")


def classificar(respostas):
    """A: à vista, ou financiado com entrada > R$ 10 mil, e compra em até 30 dias. C: só pesquisando. B: o resto."""
    prazo = respostas.get("prazo")
    if prazo == "pesquisando":
        return "C"
    pagamento, entrada = respostas.get("pagamento"), respostas.get("entrada")
    forte = pagamento == "a_vista" or (pagamento == "financiado" and entrada in ("10_30", "acima_30"))
    if forte and prazo in ("semana", "30_dias"):
        return "A"
    return "B"


def preco_brl(valor):
    if valor is None:
        return "Sob consulta"
    return "R$ " + f"{Decimal(valor):,.0f}".replace(",", ".")


def parcela_estimada(preco, entrada_pct=30, parcelas=60, taxa_am=1.99):
    """(parcela, valor da entrada) pela Tabela Price, ou (None, None). É uma ESTIMATIVA: sem IOF, tarifas nem análise de crédito."""
    if preco is None or Decimal(preco) <= 0:
        return None, None
    preco = Decimal(preco)
    entrada = (preco * Decimal(entrada_pct) / 100).quantize(Decimal("1"))
    financiado = preco - entrada
    i = Decimal(str(taxa_am)) / 100
    if i == 0:
        return (financiado / parcelas).quantize(Decimal("1")), entrada
    fator = (1 - (1 + i) ** (-parcelas))
    return (financiado * i / fator).quantize(Decimal("1")), entrada


def saudacao(agora=None):
    from django.utils import timezone
    h = timezone.localtime(agora or timezone.now()).hour
    return "Bom dia" if h < 12 else "Boa tarde" if h < 18 else "Boa noite"


def simulacao_txt(sim):
    """'entrada de R$ 20.697 e 60x de R$ 1.386' a partir de {'entrada':..., 'parcelas':..., 'parcela':...}."""
    if not sim:
        return ""
    return f"entrada de {preco_brl(sim.get('entrada'))} e {sim.get('parcelas')}x de {preco_brl(sim.get('parcela'))}"


def mensagem_whatsapp(veiculo, respostas, nome="", agora=None):
    """Mensagem inicial do cliente para a loja (tom profissional). Resume só o que o cliente respondeu."""
    primeiro = (nome or "").split()[0].title() if (nome or "").strip() else ""
    visita = respostas.get("intencao") == "visita"
    pedido = ("gostaria de agendar uma visita para conhecer o veículo." if visita
              else "gostaria de receber mais informações.")
    linhas = [f"{saudacao(agora)}! Tenho interesse no {veiculo.marca_fmt} {veiculo.modelo_fmt} {veiculo.ano}, "
              f"anunciado por {preco_brl(veiculo.valor_venda)}, e {pedido}", ""]
    if primeiro:
        linhas += [f"Meu nome é {primeiro}.", ""]
    perfil = []
    if respostas.get("pagamento"):
        perfil.append(f"• Forma de pagamento: {rotulo(PAGAMENTO, respostas.get('pagamento'))}")
    if respostas.get("entrada"):
        perfil.append(f"• Entrada: {rotulo(ENTRADA, respostas.get('entrada'))}")
    if respostas.get("troca"):
        troca_txt = "Não" if respostas.get("troca") != "sim" else (f"Sim ({respostas.get('troca_carro')})" if respostas.get("troca_carro") else "Sim")
        perfil.append(f"• Carro ou moto na troca: {troca_txt}")
    if respostas.get("prazo"):
        perfil.append(f"• Previsão de compra: {rotulo(PRAZO, respostas.get('prazo'))}")
    if respostas.get("simulacao"):
        perfil.append(f"• Simulação que fiz no site: {simulacao_txt(respostas['simulacao'])} (estimativa)")
    if perfil:
        linhas += ["Segue um resumo do meu perfil:"] + perfil + [""]
    linhas.append("Aguardo o seu retorno. Obrigado(a).")
    return "\n".join(linhas)


def link_whatsapp(numero, texto):
    return f"https://wa.me/{numero}?text={quote(texto)}"


def slug_veiculo(v):
    base = f"{v.marca}-{v.modelo}-{v.ano}"
    return re.sub(r"[^a-z0-9]+", "-", sem_acento(base).lower()).strip("-")


def parse_valor(texto):
    """'59.900,00', '59900,00', 'R$ 59900' ou '59900.50' -> Decimal (None se inválido)."""
    s = (texto or "").replace("R$", "").replace(" ", "").strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except Exception:  # noqa: BLE001
        return None
