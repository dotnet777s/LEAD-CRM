import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Lead(models.Model):
    """Lead do CRM (tabela própria; o estoque só é lido, via catalogo.Veiculo)."""

    class Etapa(models.TextChoices):
        NOVO = "novo", "Novo"
        REQUALIFICAR = "requalificar", "Requalificar"
        ATENDIMENTO = "atendimento", "Em atendimento"
        SEM_ESTOQUE = "sem_estoque", "Não temos"
        QUALIFICADO = "qualificado", "Qualificado"
        VISITA = "visita", "Visita agendada"
        COMPARECEU = "compareceu", "Compareceu"
        NEGOCIACAO = "negociacao", "Negociação"
        VENDIDO = "vendido", "Vendido"
        PERDIDO = "perdido", "Perdido"

    # REQUALIFICAR: quem deixou o contato mas não terminou o formulário. Fica fora do funil de vendas (taxas e 'avançar').
    ABERTAS = [Etapa.NOVO, Etapa.REQUALIFICAR, Etapa.ATENDIMENTO, Etapa.SEM_ESTOQUE, Etapa.QUALIFICADO, Etapa.VISITA, Etapa.COMPARECEU, Etapa.NEGOCIACAO]
    # Ordem do funil (quadro, botão "avançar" e taxas do painel)
    FUNIL = [Etapa.NOVO, Etapa.ATENDIMENTO, Etapa.QUALIFICADO, Etapa.VISITA, Etapa.COMPARECEU, Etapa.NEGOCIACAO, Etapa.VENDIDO]

    class Classe(models.TextChoices):
        A = "A", "A · quente"
        B = "B", "B · morno"
        C = "C", "C · frio"

    class Origem(models.TextChoices):
        META_LP = "meta_lp", "Anúncio Meta (página)"
        WHATSAPP = "whatsapp", "WhatsApp direto"
        PORTAL = "portal", "Portal (OLX, Mobiauto...)"
        LOJA = "loja", "Loja"
        INDICACAO = "indicacao", "Indicação"
        OUTRO = "outro", "Outro"

    class Portal(models.TextChoices):
        NAPISTA = "napista", "Napista"
        MOBIAUTO = "mobiauto", "Mobiauto"
        OLX = "olx", "OLX"

    class MotivoPerda(models.TextChoices):
        PRECO = "preco", "Preço"
        CREDITO = "credito_negado", "Crédito negado"
        COMPROU_OUTRO = "comprou_outro", "Comprou em outro lugar"
        SEM_RESPOSTA = "sem_resposta", "Sem resposta"
        SO_PESQUISANDO = "so_pesquisando", "Só pesquisando"
        OUTRO = "outro", "Outro"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    nome = models.CharField(max_length=120)
    telefone = models.CharField("WhatsApp", max_length=13, db_index=True,
                                help_text="Só dígitos, com 55 (ex.: 5581999999999).")
    # Carro de interesse (estoque é lido, nunca escrito). Sem constraint: a tabela é de outro sistema.
    veiculo = models.ForeignKey("catalogo.Veiculo", null=True, blank=True, on_delete=models.SET_NULL,
                                db_constraint=False, related_name="leads_crm")
    carro_texto = models.CharField("Carro de interesse", max_length=150, blank=True,
                                   help_text="Cópia do nome/preço no momento da captura.")
    origem = models.CharField(max_length=12, choices=Origem.choices, default=Origem.META_LP, db_index=True)
    # db_default: o código antigo (servidor ainda na versão anterior) continua inserindo leads sem estes campos
    portal = models.CharField(max_length=10, choices=Portal.choices, blank=True, db_default="", db_index=True)
    email = models.EmailField(blank=True, db_default="")
    nao_contatar = models.BooleanField("Não quer receber mensagens", default=False, db_default=False)
    etiquetas = models.ManyToManyField("Etiqueta", blank=True, related_name="leads")
    aniversario = models.DateField("Aniversário", null=True, blank=True)
    comentario_fixo = models.CharField("Comentário fixado na conversa", max_length=300, blank=True, db_default="")
    fixada = models.BooleanField("Conversa fixada no topo", default=False, db_default=False)
    arquivada = models.BooleanField("Conversa finalizada (arquivada)", default=False, db_default=False)
    marcada_nao_lida = models.BooleanField(default=False, db_default=False)
    classe = models.CharField(max_length=1, choices=Classe.choices, default=Classe.B)
    etapa = models.CharField(max_length=12, default=Etapa.NOVO, db_index=True)    # chave de uma EtapaFunil (editável no funil)
    etapa_desde = models.DateTimeField(default=timezone.now)
    vendedor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="leads_crm")

    respostas = models.JSONField(default=dict, blank=True)

    # funil
    primeira_resposta_em = models.DateTimeField(null=True, blank=True)
    visita_em = models.DateTimeField("Visita marcada para", null=True, blank=True)
    proximo_contato = models.DateTimeField(null=True, blank=True)
    motivo_perda = models.CharField(max_length=16, choices=MotivoPerda.choices, blank=True)
    veiculo_vendido = models.ForeignKey("catalogo.Veiculo", null=True, blank=True, on_delete=models.SET_NULL,
                                        db_constraint=False, related_name="vendas_crm")
    valor_venda = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    vendido_em = models.DateTimeField(null=True, blank=True)
    observacao = models.TextField(blank=True)

    # tracking (Meta)
    utm_source = models.CharField(max_length=120, blank=True)
    utm_medium = models.CharField(max_length=120, blank=True)
    utm_campaign = models.CharField(max_length=200, blank=True)
    utm_term = models.CharField(max_length=200, blank=True)
    utm_content = models.CharField(max_length=200, blank=True, db_index=True)
    fbclid = models.CharField(max_length=500, blank=True)
    fbp = models.CharField(max_length=200, blank=True)
    fbc = models.CharField(max_length=500, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    url_origem = models.CharField(max_length=1000, blank=True)
    event_id_lead = models.CharField("event_id do Lead (Pixel/CAPI)", max_length=80, blank=True)

    # LGPD
    consentimento_em = models.DateTimeField(null=True, blank=True)
    consentimento_ip = models.GenericIPAddressField(null=True, blank=True)
    consent_meta = models.BooleanField(default=False)     # aceitou cookies de marketing: só então enviamos eventos à Meta

    criado_em = models.DateTimeField(default=timezone.now, db_index=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.nome} ({self.telefone})"

    @property
    def de_anuncio(self):
        """Só leads vindos da página/anúncio enviam eventos de CRM para a Meta."""
        return self.origem == self.Origem.META_LP or bool(self.fbc or self.fbp)

    @property
    def whatsapp_url(self):
        return f"https://wa.me/{self.telefone}" if self.telefone else ""

    @property
    def telefone_formatado(self):
        t = self.telefone[2:] if self.telefone.startswith("55") else self.telefone
        if len(t) == 11:
            return f"({t[:2]}) {t[2:7]}-{t[7:]}"
        if len(t) == 10:
            return f"({t[:2]}) {t[2:6]}-{t[6:]}"
        return self.telefone

    def get_etapa_display(self):
        from . import funil
        return funil.nome(self.etapa)

    @property
    def etapa_cor(self):
        from . import funil
        return funil.cor(self.etapa)

    @property
    def proxima_etapa(self):
        """Chave da próxima etapa do funil (ou None). Etapas laterais voltam para 'Em atendimento'."""
        from . import funil
        e = funil.proxima(self.etapa)
        return e.chave if e else None

    @property
    def incompleto(self):
        return bool((self.respostas or {}).get("parcial"))

    @property
    def whatsapp_requalificar_url(self):
        """Link do WhatsApp com a mensagem para retomar quem não terminou o formulário (escrita pela loja, formal)."""
        if not self.telefone:
            return ""
        from urllib.parse import quote
        primeiro = (self.nome or "").split()[0].title() if (self.nome or "").strip() else ""
        carro = f" do {self.carro_texto.split(' · ')[0]}" if self.carro_texto else ""
        ola = f"Olá, {primeiro}!" if primeiro else "Olá!"
        texto = (f"{ola} Aqui é da TopCar Veículos. Vi que você começou a simulação{carro} e gostaria de ajudar a concluí-la. "
                 "Posso te passar as condições?")
        return f"https://wa.me/{self.telefone}?text={quote(texto)}"


class Interacao(models.Model):
    """Novo envio do formulário pelo mesmo telefone em até 30 dias (não cria outro card)."""
    lead = models.ForeignKey(Lead, related_name="interacoes", on_delete=models.CASCADE)
    veiculo = models.ForeignKey("catalogo.Veiculo", null=True, blank=True, on_delete=models.SET_NULL,
                                db_constraint=False, related_name="+")
    respostas = models.JSONField(default=dict, blank=True)
    utm_content = models.CharField(max_length=200, blank=True)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-criado_em"]


class Historico(models.Model):
    """Linha do tempo do lead: etapa, nota, consultor e eventos enviados à Meta."""

    class Tipo(models.TextChoices):
        CRIADO = "criado", "Lead criado"
        ETAPA = "etapa", "Mudança de etapa"
        NOTA = "nota", "Nota"
        CONSULTOR = "consultor", "Troca de consultor"
        META = "meta", "Evento Meta"
        FORM = "form", "Novo envio do formulário"

    lead = models.ForeignKey(Lead, related_name="historico", on_delete=models.CASCADE)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    de_etapa = models.CharField(max_length=12, blank=True)
    para_etapa = models.CharField(max_length=12, blank=True)
    duracao_etapa = models.DurationField(null=True, blank=True)
    texto = models.CharField(max_length=500, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    criado_em = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-criado_em", "-id"]


class EventoMeta(models.Model):
    """Outbox da Conversions API: nada é enviado na requisição; o worker lê esta tabela."""

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        ENVIADO = "enviado", "Enviado"
        ERRO = "erro", "Erro (esgotou tentativas)"
        NAO_ENVIADO = "nao_enviado", "Não enviado"

    lead = models.ForeignKey(Lead, null=True, blank=True, related_name="eventos_meta", on_delete=models.SET_NULL)
    nome = models.CharField(max_length=40, db_index=True)
    event_id = models.CharField(max_length=120, unique=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    tentativas = models.PositiveSmallIntegerField(default=0)
    proxima_tentativa_em = models.DateTimeField(default=timezone.now, db_index=True)
    resposta = models.TextField(blank=True)
    motivo = models.CharField(max_length=255, blank=True)
    criado_em = models.DateTimeField(default=timezone.now)
    enviado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]


class Investimento(models.Model):
    """Verba de anúncios do mês (informada à mão) para custo por lead, por qualificado e por venda."""
    mes = models.DateField(unique=True, help_text="Sempre o dia 1 do mês.")
    valor = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["-mes"]


class TelegramVinculo(models.Model):
    """Liga um usuário do CRM a um chat do Telegram (avisos de lead, lembretes)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram")
    chat_id = models.BigIntegerField(null=True, blank=True, unique=True)
    codigo = models.CharField(max_length=12, blank=True)
    codigo_expira_em = models.DateTimeField(null=True, blank=True)
    vinculado_em = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} -> {self.chat_id or 'sem chat'}"


class NotificacaoTelegram(models.Model):
    """Fila de mensagens do bot (enviada pelo worker, nunca na requisição). `chave` evita aviso duplicado."""

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        ENVIADO = "enviado", "Enviado"
        ERRO = "erro", "Erro"

    chave = models.CharField(max_length=120, unique=True, null=True, blank=True)
    tipo = models.CharField(max_length=20, blank=True)
    chat_id = models.BigIntegerField()
    texto = models.TextField()
    botoes = models.JSONField(default=list, blank=True)
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL, related_name="avisos_telegram")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    tentativas = models.PositiveSmallIntegerField(default=0)
    proxima_tentativa_em = models.DateTimeField(default=timezone.now)
    criado_em = models.DateTimeField(default=timezone.now)
    enviado_em = models.DateTimeField(null=True, blank=True)
    motivo = models.CharField(max_length=300, blank=True)


class EventoFunil(models.Model):
    """Passo do formulário da página de captura (para medir onde as pessoas desistem e comparar as variantes A/B).
    Sem consentimento: sem identificador (sid vazio) e sempre variante A."""
    ETAPAS = ["view", "comecou", "contato", "q_pagamento", "q_entrada", "q_troca", "q_prazo", "enviou"]
    criado_em = models.DateTimeField(auto_now_add=True)
    veiculo_id = models.IntegerField(null=True, blank=True)
    etapa = models.CharField(max_length=20)
    variante = models.CharField(max_length=2, default="A")
    sid = models.CharField(max_length=32, blank=True)
    consent = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["criado_em", "etapa"])]


class PortalMensagem(models.Model):
    """Registro de cada e-mail lido da caixa dos portais (evita processar duas vezes e permite revisar o que falhou)."""

    class Status(models.TextChoices):
        CRIADO = "criado", "Lead criado"
        ATUALIZADO = "atualizado", "Lead já existia"
        SEM_TELEFONE = "sem_telefone", "Sem telefone (revisar)"
        IGNORADO = "ignorado", "Não é de portal"

    message_id = models.CharField(max_length=250, unique=True)
    portal = models.CharField(max_length=10, choices=Lead.Portal.choices, blank=True)
    remetente = models.CharField(max_length=250, blank=True)
    assunto = models.CharField(max_length=300, blank=True)
    corpo = models.TextField(blank=True)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.CRIADO, db_index=True)
    identificador = models.CharField("Identificador do lead no portal", max_length=80, blank=True, db_index=True)
    erro = models.CharField(max_length=300, blank=True)
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL, related_name="portal_mensagens")
    criado_em = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-criado_em"]


# ===================================================================== follow-up e WhatsApp
class FollowUpConfig(models.Model):
    """Regras do follow-up (uma linha só). Editável na tela Follow-up > Configurar."""
    ativo = models.BooleanField(default=True)
    hora_inicio = models.PositiveSmallIntegerField("Enviar a partir das (h)", default=8)
    hora_fim = models.PositiveSmallIntegerField("Enviar até as (h)", default=20)
    enviar_domingo = models.BooleanField(default=False)
    primeiro_nome = models.BooleanField("Chamar só pelo primeiro nome", default=True)
    envio_automatico = models.BooleanField("Enviar sozinho pela API do WhatsApp (quando conectada)", default=False)
    etapas_gatilho = models.JSONField(default=list, blank=True)   # etapas em que o lead sem resposta entra no follow-up
    vale_desde = models.DateTimeField(null=True, blank=True)       # só vale para quem entrou na etapa depois desta data

    ETAPAS_PADRAO = ["novo", "requalificar", "atendimento"]

    @classmethod
    def obter(cls):
        c, _ = cls.objects.get_or_create(pk=1)
        if not c.etapas_gatilho:        # primeira vez: vale só para quem entrar nessas etapas daqui para a frente
            c.etapas_gatilho, c.vale_desde = list(cls.ETAPAS_PADRAO), timezone.now()
            c.save(update_fields=["etapas_gatilho", "vale_desde"])
        return c


class FollowUpPasso(models.Model):
    """Cada tentativa da sequência, com até 3 variações de texto (A/B/C) para comparar qual responde mais."""
    ordem = models.PositiveSmallIntegerField(unique=True)
    ativo = models.BooleanField(default=True)
    espera_dias = models.PositiveSmallIntegerField(default=0, help_text="Dias depois do passo anterior (0 = usa as horas).")
    espera_horas = models.PositiveSmallIntegerField(default=0)
    hora_envio = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Hora fixa do dia (vazio = mesma hora).")
    variantes = models.JSONField(default=list)
    template_nome = models.CharField("Template aprovado na Meta", max_length=100, blank=True,
                                     help_text="Usado quando a conversa está fora da janela de 24 h (API oficial).")

    class Meta:
        ordering = ["ordem"]


class FollowUp(models.Model):
    class Status(models.TextChoices):
        ATIVO = "ativo", "Em andamento"
        PAUSADO = "pausado", "Pausado"
        RESPONDEU = "respondeu", "Cliente respondeu"
        ESGOTADO = "esgotado", "Sem resposta"
        ENCERRADO = "encerrado", "Encerrado (lead avançou)"

    lead = models.ForeignKey(Lead, related_name="followups", on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ATIVO, db_index=True)
    passo_atual = models.PositiveSmallIntegerField(default=1, help_text="Ordem do próximo passo a executar.")
    variante = models.PositiveSmallIntegerField(default=0)
    proximo_em = models.DateTimeField(null=True, blank=True, db_index=True)
    iniciado_em = models.DateTimeField(default=timezone.now)
    finalizado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["proximo_em"]


class FollowUpTentativa(models.Model):
    followup = models.ForeignKey(FollowUp, related_name="tentativas", on_delete=models.CASCADE)
    passo = models.PositiveSmallIntegerField()
    variante = models.PositiveSmallIntegerField(default=0)
    mensagem = models.TextField(blank=True)
    via_api = models.BooleanField(default=False)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    feita_em = models.DateTimeField(default=timezone.now)
    respondeu = models.BooleanField(default=False)

    class Meta:
        ordering = ["feita_em"]


class WhatsAppMensagem(models.Model):
    """Conversa do WhatsApp (API oficial): entradas pelo webhook e saídas enviadas pelo CRM."""

    class Direcao(models.TextChoices):
        ENTRADA = "in", "Cliente"
        SAIDA = "out", "Loja"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Enviando"
        ENVIADA = "sent", "Enviada"
        ENTREGUE = "delivered", "Entregue"
        LIDA = "read", "Lida"
        FALHOU = "failed", "Falhou"
        RECEBIDA = "recebida", "Recebida"

    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL, related_name="mensagens_wa")
    telefone = models.CharField(max_length=15, db_index=True)
    direcao = models.CharField(max_length=3, choices=Direcao.choices)
    tipo = models.CharField(max_length=20, default="text")          # text, template, image, audio, ...
    texto = models.TextField(blank=True)
    wamid = models.CharField(max_length=200, blank=True, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECEBIDA)
    erro = models.CharField(max_length=300, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    midia_id = models.CharField(max_length=200, blank=True)      # id da mídia na Meta (imagem, áudio, documento...)
    midia_tipo = models.CharField(max_length=100, blank=True)    # mime type
    lida = models.BooleanField(default=True, db_index=True)      # só as mensagens de ENTRADA nascem como não lidas
    criado_em = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["criado_em", "id"]


class RespostaRapida(models.Model):
    """Textos prontos do atendimento (ex.: endereço da loja, documentos para financiamento)."""
    titulo = models.CharField(max_length=60)
    texto = models.TextField(max_length=1500)
    ordem = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["ordem", "titulo"]


class ModeloWhatsApp(models.Model):
    """Template de mensagem da Meta (necessário para falar fora da janela de 24 h e para campanhas)."""
    nome = models.CharField(max_length=100)
    idioma = models.CharField(max_length=10, default="pt_BR")
    categoria = models.CharField(max_length=20, blank=True)       # UTILITY, MARKETING, AUTHENTICATION
    status = models.CharField(max_length=20, blank=True)          # APPROVED, PENDING, REJECTED...
    corpo = models.TextField(blank=True)
    n_variaveis = models.PositiveSmallIntegerField(default=0)
    corpo_local = models.TextField(blank=True)                       # texto com variáveis nomeadas, ex.: {{nome}}
    variaveis = models.JSONField(default=list, blank=True)           # ordem das variáveis: [{{1}}, {{2}}...] -> nomes
    header_tipo = models.CharField(max_length=10, blank=True)        # IMAGE ou VIDEO (cabeçalho de mídia)
    header_url = models.CharField(max_length=500, blank=True)        # link público da mídia usada no envio
    atualizado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["nome"]
        unique_together = [("nome", "idioma")]

    @property
    def aprovado(self):
        return self.status == "APPROVED"


class Campanha(models.Model):
    """Disparo de um template para um grupo de leads (ex.: quem ficou sem resposta nos últimos 30 dias)."""

    class Status(models.TextChoices):
        RASCUNHO = "rascunho", "Rascunho"
        ENVIANDO = "enviando", "Enviando"
        PAUSADA = "pausada", "Pausada"
        CONCLUIDA = "concluida", "Concluída"

    class Tipo(models.TextChoices):
        TODOS = "todos", "Todos os contatos"
        REGUA = "regua", "Régua de disparos"
        TAG = "tag", "Por etiqueta"
        ETAPA = "etapa", "Por etapa do funil"
        CADASTRO = "cadastro", "Por leads cadastrados"
        ANIVERSARIO = "aniversario", "Data de aniversário"
        AVISE = "avise", "Avise quando chegar"
        ITEM = "item", "Item do catálogo"

    nome = models.CharField(max_length=100)
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.ETAPA, db_default="etapa")
    config = models.JSONField(default=dict, blank=True)     # régua: etiquetas e passos; item: veículo; avise/aniversário: opções
    modelo = models.ForeignKey(ModeloWhatsApp, on_delete=models.PROTECT, related_name="campanhas")
    filtro = models.JSONField(default=dict, blank=True)           # etapa, origem, portal, dias_sem_contato
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RASCUNHO, db_index=True)
    criada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    criada_em = models.DateTimeField(default=timezone.now)
    iniciada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-criada_em"]


class CampanhaEnvio(models.Model):
    class Status(models.TextChoices):
        PENDENTE = "pendente", "Na fila"
        ENVIADO = "enviado", "Enviado"
        FALHOU = "falhou", "Falhou"

    campanha = models.ForeignKey(Campanha, related_name="envios", on_delete=models.CASCADE)
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="envios_campanha")
    modelo = models.ForeignKey(ModeloWhatsApp, null=True, blank=True, on_delete=models.PROTECT, related_name="+")   # passo da régua
    passo = models.PositiveSmallIntegerField(default=0)
    enviar_em = models.DateTimeField(null=True, blank=True, db_index=True)
    extras = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    erro = models.CharField(max_length=300, blank=True)
    enviado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("campanha", "lead", "passo")]


# ===================================================================== etiquetas e classificação automática
class Etiqueta(models.Model):
    """Etiqueta colorida do lead (como no Avise Aí). Pode ser posta à mão ou pelas regras automáticas."""
    nome = models.CharField(max_length=40, unique=True)
    cor = models.CharField(max_length=7, default="#2ee66b")
    bloqueia_followup = models.BooleanField("Impede o follow-up automático", default=False)
    ordem = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["ordem", "nome"]

    def __str__(self):
        return self.nome


class RegraClassificacao(models.Model):
    """Se a mensagem do cliente tiver uma das palavras: põe a etiqueta e/ou leva o lead para a etapa (só avança)."""
    nome = models.CharField(max_length=60)
    palavras = models.TextField(help_text="Uma por linha ou separadas por vírgula. Sem diferenciar acento e maiúscula.")
    etiqueta = models.ForeignKey(Etiqueta, null=True, blank=True, on_delete=models.SET_NULL, related_name="regras")
    etapa = models.CharField(max_length=12, blank=True)
    motivo_perda = models.CharField(max_length=16, blank=True, choices=Lead.MotivoPerda.choices)
    ativa = models.BooleanField(default=True)
    ordem = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["ordem", "id"]


class MensagemProgramada(models.Model):
    """Mensagem de WhatsApp para sair depois (ex.: amanhã às 9h). Sai pela API, dentro da janela de 24 h."""

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Programada"
        ENVIADA = "enviada", "Enviada"
        FALHOU = "falhou", "Falhou"
        CANCELADA = "cancelada", "Cancelada"

    lead = models.ForeignKey(Lead, related_name="programadas", on_delete=models.CASCADE)
    texto = models.TextField(max_length=1500)
    enviar_em = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    erro = models.CharField(max_length=300, blank=True)
    criada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    criada_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["enviar_em"]


class Aviso(models.Model):
    """Envio avulso de um modelo para um contato, em data e hora marcadas, com recorrência (tela Enviar)."""

    class Recorrencia(models.TextChoices):
        NENHUMA = "", "Sem recorrência"
        DIARIA = "diaria", "Diária"
        SEMANAL = "semanal", "Semanal"
        QUINZENAL = "quinzenal", "Quinzenal"
        MENSAL = "mensal", "Mensal"
        TRIMESTRAL = "trimestral", "Trimestral"
        SEMESTRAL = "semestral", "Semestral"
        ANUAL = "anual", "Anual"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        ENVIADO = "enviado", "Enviado"
        FALHOU = "falhou", "Falhou"
        CANCELADO = "cancelado", "Cancelado"

    lead = models.ForeignKey(Lead, related_name="avisos", on_delete=models.CASCADE)
    modelo = models.ForeignKey(ModeloWhatsApp, on_delete=models.PROTECT, related_name="avisos")
    enviar_em = models.DateTimeField(db_index=True)
    recorrencia = models.CharField(max_length=10, choices=Recorrencia.choices, blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    erro = models.CharField(max_length=300, blank=True)
    enviado_em = models.DateTimeField(null=True, blank=True)
    arquivado = models.BooleanField(default=False)
    origem = models.CharField(max_length=30, blank=True)               # "campanha: nome" quando vier de uma campanha
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-enviar_em", "-id"]


class Procura(models.Model):
    """Cliente que procurou um carro que a loja não tinha. Vira aviso quando um carro parecido entra no estoque."""
    lead = models.ForeignKey(Lead, related_name="procuras", on_delete=models.CASCADE)
    descricao = models.CharField(max_length=150)
    avisada_em = models.DateTimeField(null=True, blank=True)
    criada_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-criada_em"]


# ===================================================================== agenda (profissionais, atividades, agendamentos)
class Profissional(models.Model):
    nome = models.CharField(max_length=80)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        verbose_name_plural = "profissionais"

    def __str__(self):
        return self.nome


class Atividade(models.Model):
    """Tipo de compromisso (ex.: Visita à loja, Test drive, Avaliação do carro). A duração preenche o agendamento."""
    nome = models.CharField(max_length=80)
    duracao_min = models.PositiveSmallIntegerField(default=30)
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]


class Agendamento(models.Model):
    class Status(models.TextChoices):
        AGENDADO = "agendado", "Agendado"
        CONFIRMADO = "confirmado", "Confirmado"
        REALIZADO = "realizado", "Realizado"
        CANCELADO = "cancelado", "Cancelado"
        FALTOU = "faltou", "Não compareceu"

    lead = models.ForeignKey(Lead, related_name="agendamentos", on_delete=models.CASCADE)
    profissional = models.ForeignKey(Profissional, null=True, blank=True, on_delete=models.SET_NULL, related_name="agendamentos")
    atividade = models.ForeignKey(Atividade, null=True, blank=True, on_delete=models.SET_NULL, related_name="agendamentos")
    inicio = models.DateTimeField(db_index=True)
    fim = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.AGENDADO, db_index=True)
    observacao = models.CharField(max_length=300, blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["inicio"]


class EtapaFunil(models.Model):
    """Etapa do funil (coluna do Kanban), totalmente editável: nome, cor, ordem, tag que leva o lead até ela,
    grupos de palavras-chave e gatilho por arquivo. As etapas 'de sistema' (novo, atendimento, qualificado, visita,
    vendido, perdido) não podem ser apagadas porque o CRM usa as regras delas (eventos da Meta, formulários, painel)."""

    class Tipo(models.TextChoices):
        NORMAL = "normal", "Normal"
        GANHO = "ganho", "Ganho (venda)"
        PERDIDO = "perdido", "Perdido"

    chave = models.CharField(max_length=12, unique=True)
    nome = models.CharField(max_length=40)
    cor = models.CharField(max_length=7, default="#3b82f6")
    ordem = models.PositiveSmallIntegerField(default=0, db_index=True)
    tipo = models.CharField(max_length=8, choices=Tipo.choices, default=Tipo.NORMAL)
    lateral = models.BooleanField(default=False, help_text="Fica fora da sequência 'avançar' (ex.: Não temos).")
    sistema = models.BooleanField(default=False)
    tag = models.ForeignKey(Etiqueta, null=True, blank=True, on_delete=models.SET_NULL, related_name="etapas")
    grupos = models.JSONField(default=list, blank=True)       # [{"palavras": ["preço", "valor"], "todas": false}]
    por_arquivo = models.BooleanField(default=False)          # cliente mandou um arquivo/foto -> vai para esta etapa

    class Meta:
        ordering = ["ordem", "id"]

    def __str__(self):
        return self.nome


class AgendaConfig(models.Model):
    """Mensagens automáticas do agendamento (uma linha só). Texto livre vai dentro da janela de 24 h; fora dela, o modelo aprovado."""
    confirmar = models.BooleanField("Confirmar o agendamento assim que for marcado", default=True)
    lembrete_24h = models.BooleanField("Lembrete 1 dia antes", default=True)
    lembrete_2h = models.BooleanField("Lembrete 2 horas antes", default=True)
    pos_visita = models.BooleanField("Agradecer depois da visita", default=True)
    txt_confirmacao = models.TextField(default="Olá, {nome}! Sua visita à TopCar Veículos está agendada para {dia}, {data}, às {hora}. "
                                               "Endereço: {endereco}. Qualquer imprevisto, é só avisar por aqui.")
    txt_lembrete = models.TextField(default="Olá, {nome}! Lembrando da sua visita à TopCar Veículos {quando}, às {hora}. "
                                            "Endereço: {endereco}. Te esperamos!")
    txt_pos = models.TextField(default="Olá, {nome}! Obrigado pela visita à TopCar Veículos. Se ficou alguma dúvida ou quiser simular "
                                       "o pagamento, é só me chamar por aqui.")
    modelo_confirmacao = models.ForeignKey("ModeloWhatsApp", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    modelo_lembrete = models.ForeignKey("ModeloWhatsApp", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    modelo_pos = models.ForeignKey("ModeloWhatsApp", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    @classmethod
    def obter(cls):
        c, _ = cls.objects.get_or_create(pk=1)
        return c


class AgendamentoMensagem(models.Model):
    """Cada mensagem automática de um agendamento (confirmação, lembretes e agradecimento) e o que aconteceu com ela."""

    class Tipo(models.TextChoices):
        CONFIRMACAO = "confirmacao", "Confirmação"
        LEMBRETE_24H = "lembrete_24h", "Lembrete (1 dia antes)"
        LEMBRETE_2H = "lembrete_2h", "Lembrete (2 h antes)"
        POS = "pos", "Agradecimento"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Programada"
        ENVIADA = "enviada", "Enviada"
        FALHOU = "falhou", "Não enviada"
        CANCELADA = "cancelada", "Cancelada"

    agendamento = models.ForeignKey(Agendamento, related_name="mensagens", on_delete=models.CASCADE)
    tipo = models.CharField(max_length=14, choices=Tipo.choices)
    enviar_em = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    erro = models.CharField(max_length=300, blank=True)
    enviada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["enviar_em"]
        unique_together = [("agendamento", "tipo")]
