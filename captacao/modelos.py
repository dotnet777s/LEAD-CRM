"""Variáveis dos modelos de mensagem ({{nome}}, {{saudacao}}...) como no Avise Aí.

O usuário escreve o texto com nomes legíveis; para a Meta o texto vai com {{1}}, {{2}}... e guardamos a ordem
em `variaveis`. No envio, cada número é preenchido com o dado do contato."""
import re
from datetime import datetime

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from . import utils

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

# nome -> (descrição na tela, exemplo que a Meta exige ao aprovar)
VARIAVEIS = {
    "nome": ("Primeiro nome do contato", "Maria"),
    "saudacao": ("Bom dia / Boa tarde / Boa noite", "Bom dia"),
    "remetente": ("Nome da loja", "TopCar Veículos"),
    "meu_whatsapp": ("WhatsApp da loja", "(81) 96000-2209"),
    "endereco": ("Endereço da loja", "Loja 11, Shopping do Automóvel, Olinda"),
    "carro": ("Carro de interesse do contato", "Fiat Argo 2022"),
    "preco": ("Preço do carro de interesse", "R$ 59.900"),
    "link": ("Link da página do carro", "https://contato.topcarpe.com.br/v/1"),
    "mes": ("Mês atual", "novembro"),
    "descricao": ("Texto livre (campanhas e avisos)", "revisão gratuita"),
    "valor": ("Valor livre (campanhas e avisos)", "R$ 500"),
    "vencimento": ("Data livre (campanhas e avisos)", "30/11"),
}
RE_VAR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def nomes_no_texto(texto):
    """Variáveis usadas, na ordem em que aparecem e sem repetir."""
    vistos = []
    for n in RE_VAR.findall(texto or ""):
        if n not in vistos:
            vistos.append(n)
    return vistos


def invalidas(texto):
    return [n for n in nomes_no_texto(texto) if n not in VARIAVEIS]


def para_meta(texto):
    """'Oi {{nome}}, {{saudacao}}!' -> ('Oi {{1}}, {{2}}!', ['nome', 'saudacao'], ['Maria', 'Bom dia'])."""
    ordem = nomes_no_texto(texto)
    corpo = RE_VAR.sub(lambda m: "{{%d}}" % (ordem.index(m.group(1)) + 1), texto or "")
    return corpo, ordem, [VARIAVEIS[n][1] for n in ordem if n in VARIAVEIS]


def valor(nome, lead, extras=None, agora=None):
    extras = extras or {}
    if nome in extras and extras[nome]:
        return str(extras[nome])
    agora = agora or timezone.localtime()
    loja = settings.LOJA
    primeiro = ((lead.nome or "").split() or [""])[0].title()
    carro = (lead.carro_texto or "").split(" · ")
    if nome == "nome":
        return primeiro or "tudo bem"
    if nome == "saudacao":
        return utils.saudacao(agora)
    if nome == "remetente":
        return loja.get("nome", "TopCar Veículos")
    if nome == "meu_whatsapp":
        return loja.get("whatsapp_fmt", "")
    if nome == "endereco":
        return loja.get("endereco", "")
    if nome == "carro":
        return carro[0] or "o carro que você viu"
    if nome == "preco":
        return carro[1] if len(carro) > 1 else ""
    if nome == "link":
        if lead.veiculo_id:
            return settings.SITE_URL + reverse("captacao:captura", args=[lead.veiculo_id, utils.slug_veiculo(lead.veiculo)])
        return settings.SITE_URL + reverse("captacao:catalogo")
    if nome == "mes":
        return MESES[agora.month - 1]
    return str(extras.get(nome, "")) or "-"


def parametros(modelo, lead, extras=None):
    """Lista de textos para {{1}}, {{2}}... Modelos antigos (sem nomes) usam o primeiro nome em {{1}}."""
    if modelo.variaveis:
        return [valor(n, lead, extras) for n in modelo.variaveis]
    if modelo.n_variaveis:
        return [valor("nome", lead)] + ["-"] * (modelo.n_variaveis - 1)
    return []


def texto_final(modelo, params):
    """Texto como o cliente vai ler (para o histórico da conversa)."""
    t = modelo.corpo
    for i, p in enumerate(params, 1):
        t = t.replace("{{%d}}" % i, p)
    return t
