"""Entrada de leads dos portais (Napista, Mobiauto, OLX) pela caixa de e-mail (IMAP).

O portal manda um e-mail a cada contato; aqui lemos a caixa, identificamos o portal, extraímos nome, telefone, carro e
mensagem, e criamos (ou atualizamos) o lead. O formato exato de cada portal ainda será ajustado com e-mails reais:
o leitor é tolerante (rótulos "Nome:", "Telefone:"...) e, se não achar telefone, guarda a mensagem para revisão
em vez de perder o contato. Nada aqui apaga ou move e-mails: só marca como lido depois de processar."""
import email
import email.policy
import hashlib
import html
import imaplib
import re
from email.utils import parseaddr

from django.conf import settings

from . import services, utils
from .models import PortalMensagem

# remetente (domínio) -> portal. Se o e-mail for encaminhado de outra caixa, usamos palavras do assunto/corpo.
DOMINIOS = {"napista": ("napista.com.br",), "mobiauto": ("mobiauto.com.br", "mobiauto.com"), "olx": ("olx.com.br", "olxbr.com.br")}
PALAVRAS = {"napista": ("napista",), "mobiauto": ("mobiauto",), "olx": ("olx",)}

ROTULOS_NOME = r"(?:nome(?: completo)?|cliente|interessado|name)"
ROTULOS_FONE = r"(?:telefone|celular|whatsapp|fone|contato|phone|tel)"
ROTULOS_CARRO = r"(?:ve[ií]culo|carro|an[uú]ncio|modelo|produto|item)"
ROTULOS_MSG = r"(?:mensagem|message|observa[cç][aã]o|coment[aá]rio)"
RE_FONE = re.compile(r"(?<!\d)(?:\+?55[\s.-]*)?\(?\d{2}\)?[\s.-]*9?\d{4}[\s.-]?\d{4}(?!\d)")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
RE_PLACA = re.compile(r"\b([A-Z]{3}\d[A-Z0-9]\d{2})\b")


def _campo(texto, rotulos):
    m = re.search(rf"(?im)^[ \t>*-]*{rotulos}[ \t]*[:\-–][ \t]*(.+?)[ \t]*$", texto)
    return m.group(1).strip() if m else ""


def texto_do_email(msg):
    """Texto simples do e-mail (prefere text/plain; se só houver HTML, tira as tags)."""
    plano, pagina = "", ""
    for parte in msg.walk():
        tipo = parte.get_content_type()
        if parte.get_content_maintype() == "multipart" or parte.get_filename():
            continue
        try:
            conteudo = parte.get_content()
        except Exception:  # noqa: BLE001 - charset quebrado: tenta bytes
            conteudo = (parte.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if tipo == "text/plain":
            plano += conteudo + "\n"
        elif tipo == "text/html":
            pagina += conteudo + "\n"
    if plano.strip():
        return plano
    pagina = re.sub(r"(?is)<(script|style).*?</\1>", " ", pagina)
    pagina = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>|</t[dh]>", "\n", pagina)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", pagina)))


def identificar_portal(remetente, assunto, corpo):
    dominio = (parseaddr(remetente)[1].rsplit("@", 1)[-1] or "").lower()
    for portal, doms in DOMINIOS.items():
        if any(dominio == d or dominio.endswith("." + d) for d in doms):
            return portal
    base = f"{assunto}\n{corpo[:800]}".lower()
    for portal, palavras in PALAVRAS.items():
        if any(p in base for p in palavras):
            return portal
    return ""


RE_PRECO = re.compile(r"^R\$\s*([\d.]+(?:,\d{1,2})?)\s*$")
RE_ID_LEAD = re.compile(r"(?im)identificador do lead\s*:?\s*([0-9a-f-]{8,})")


def _preco(txt):
    """'119990,00' ou '119.990,00' -> 119990.0 (OLX manda o preço sem separador de milhar)."""
    t = txt.strip()
    t = t.replace(".", "").replace(",", ".") if "," in t else t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def anuncio_olx(corpo):
    """Título e preço do anúncio: a linha de preço ('R$ 119990,00') e, logo acima dela, o título do carro.
    Funciona com 'Anúncio:' seguido do título ou com o título solto antes do preço."""
    linhas = [l.strip() for l in corpo.splitlines() if l.strip()]
    for i, l in enumerate(linhas):
        m = RE_PRECO.match(l)
        if m and i > 0:
            titulo = linhas[i - 1]
            if re.match(r"(?i)^an[uú]ncio\s*:?$", titulo) and i > 1:
                titulo = linhas[i - 2]
            return titulo[:150], _preco(m.group(1))
    return "", None


def tipo_contato(corpo):
    """Como o cliente chegou: clicou no WhatsApp do anúncio, ou 'cliente busca um carro como o seu' (similar)."""
    c = corpo.lower()
    if "acessou whatsapp" in c or "acessou o whatsapp" in c:
        return "clicou no WhatsApp do anúncio"
    if "cliente busca" in c:
        return "procura um carro parecido com o anúncio"
    return "interessado no anúncio"


def extrair(assunto, corpo):
    """Nome, telefone (55+DDD+número), e-mail, carro e mensagem do cliente. Campos que não achar ficam vazios."""
    nome = _campo(corpo, ROTULOS_NOME)
    if nome and nome == nome.lower():
        nome = nome.title()
    fone_txt = _campo(corpo, ROTULOS_FONE)
    telefone = utils.normalizar_telefone(fone_txt) if fone_txt else ""
    if not telefone:
        for m in RE_FONE.finditer(corpo):
            telefone = utils.normalizar_telefone(m.group(0))
            if telefone:
                break
    dominios = [d for ds in DOMINIOS.values() for d in ds]
    emails = [e for e in RE_EMAIL.findall(corpo)
              if not any(d in e.lower() for d in dominios) and "noreply" not in e.lower() and "no-reply" not in e.lower()]
    titulo, preco = anuncio_olx(corpo)
    carro = titulo or _campo(corpo, ROTULOS_CARRO)
    if not carro:
        carro = re.sub(r"(?i)^(re|fwd?|enc):\s*", "", assunto or "").strip()
    achou = RE_PLACA.search(corpo.upper())
    idl = RE_ID_LEAD.search(corpo)
    mensagem = _campo(corpo, ROTULOS_MSG)
    if not mensagem:
        busca = re.search(r"(?im)^\s*(cliente busca .+?)\s*$", corpo)
        mensagem = busca.group(1) if busca else ""
    return {"nome": nome[:120], "telefone": telefone, "email": emails[0] if emails else "", "carro": carro[:150],
            "preco": preco, "placa": achou.group(1) if achou else "", "mensagem": mensagem[:500],
            "identificador": idl.group(1) if idl else "", "tipo": tipo_contato(corpo)}


def achar_veiculo(dados):
    """Liga ao estoque: pela placa, se vier no e-mail; senão pelo preço do anúncio + nome do modelo no título.
    Só liga quando há UM carro que bate (na dúvida, o lead fica só com o texto do anúncio)."""
    from catalogo.models import Veiculo
    if dados.get("placa"):
        v = Veiculo.objects.filter(placa__iexact=dados["placa"]).first()
        if v:
            return v
    if not dados.get("preco") or not dados.get("carro"):
        return None
    titulo = utils.sem_acento(dados["carro"]).lower()
    certos = [v for v in Veiculo.objects.filter(valor_venda=dados["preco"], status__in=Veiculo.EM_LOJA)
              if utils.sem_acento(v.modelo_fmt).lower() in titulo]
    return certos[0] if len(certos) == 1 else None


def processar(msg):
    """Processa um e-mail já lido. Devolve o PortalMensagem gravado. Idempotente pelo Message-ID."""
    mid = (msg.get("Message-ID") or "").strip()[:250]
    if not mid:
        mid = "sem-id-" + hashlib.sha1(f"{msg.get('Date')}|{msg.get('Subject')}|{msg.get('From')}".encode()).hexdigest()
    ja = PortalMensagem.objects.filter(message_id=mid).first()
    if ja:
        return ja
    assunto = str(msg.get("Subject") or "")[:300]
    remetente = str(msg.get("From") or "")[:250]
    corpo = texto_do_email(msg)
    portal = identificar_portal(remetente, assunto, corpo)
    reg = PortalMensagem(message_id=mid, portal=portal, remetente=remetente, assunto=assunto, corpo=corpo[:6000])
    if not portal or portal not in settings.PORTAIS_ATIVOS:       # portais ainda não ligados ficam só registrados
        reg.status = PortalMensagem.Status.IGNORADO
        reg.save()
        return reg
    dados = extrair(assunto, corpo)
    reg.identificador = dados["identificador"]
    if dados["identificador"] and PortalMensagem.objects.filter(portal=portal, identificador=dados["identificador"]).exists():
        reg.status, reg.erro = PortalMensagem.Status.IGNORADO, "Lead já recebido (mesmo identificador do portal)."
        reg.save()
        return reg
    if not dados["telefone"]:
        reg.status, reg.erro = PortalMensagem.Status.SEM_TELEFONE, "Telefone não encontrado no e-mail."
        reg.save()
        return reg
    lead, criado = services.criar_lead_portal(portal, dados["nome"] or "Cliente do portal", dados["telefone"], dados["carro"],
                                              achar_veiculo(dados), dados["mensagem"], dados["email"], tipo=dados["tipo"])
    reg.lead = lead
    reg.status = PortalMensagem.Status.CRIADO if criado else PortalMensagem.Status.ATUALIZADO
    reg.save()
    return reg


def ler_caixa(limite=50):
    """Lê os e-mails NÃO LIDOS da pasta configurada. Marca como lido só os que foram tratados (ou ignorados).
    Devolve (processados, erros), ou None se a leitura está desligada (sem usuário/senha)."""
    host, usuario, senha = settings.PORTAIS_IMAP_HOST, settings.PORTAIS_IMAP_USER, settings.PORTAIS_IMAP_PASS
    if not (host and usuario and senha):
        return None
    ok = erros = 0
    con = imaplib.IMAP4_SSL(host, settings.PORTAIS_IMAP_PORTA)
    try:
        con.login(usuario, senha)
        con.select(settings.PORTAIS_IMAP_PASTA)
        _, dados = con.search(None, "UNSEEN")
        for num in (dados[0].split() if dados and dados[0] else [])[:limite]:
            _, parte = con.fetch(num, "(BODY.PEEK[])")
            if not parte or not parte[0]:
                continue
            try:
                processar(email.message_from_bytes(parte[0][1], policy=email.policy.default))
                con.store(num, "+FLAGS", "\\Seen")
                ok += 1
            except Exception:  # noqa: BLE001 - um e-mail ruim não trava os outros; fica não lido para nova tentativa
                erros += 1
    finally:
        try:
            con.logout()
        except Exception:  # noqa: BLE001
            pass
    return ok, erros
