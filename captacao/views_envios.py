"""Enviar (avisos com recorrência), Histórico de envios, assistente de campanhas, agenda (profissionais, atividades,
agendamentos), conta do usuário e aniversário do contato."""
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views import View
from django.views.generic import TemplateView

from catalogo.models import Veiculo

from . import agendamentos, avisos, campanhas, funil, modelos, services, utils, whatsapp
from .models import (AgendaConfig, Agendamento, AgendamentoMensagem, Atividade, Aviso, Campanha, CampanhaEnvio, Etiqueta, Lead, ModeloWhatsApp, Profissional)
from .views import Base, eh_gestor, leads_visiveis


# ---------------------------------------------------------------- Enviar
class EnviarView(Base, TemplateView):
    template_name = "captacao/enviar.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        agora = timezone.localtime()
        ctx.update(contatos=leads_visiveis(self.request.user).filter(nao_contatar=False).exclude(telefone="").order_by("nome")[:800],
                   modelos=ModeloWhatsApp.objects.filter(status="APPROVED"), recorrencias=Aviso.Recorrencia.choices,
                   hoje=agora.strftime("%Y-%m-%d"), hora=agora.strftime("%H:%M"), api=whatsapp.configurado(),
                   erro=self.request.GET.get("erro", ""), msg=self.request.GET.get("msg", ""), lead_id=self.request.GET.get("lead", ""))
        return ctx

    def post(self, request):
        p = request.POST
        ids = [int(i) for i in p.getlist("contatos") if i.isdigit()]
        modelo = ModeloWhatsApp.objects.filter(pk=p.get("modelo", ""), status="APPROVED").first() if p.get("modelo", "").isdigit() else None
        quando = parse_datetime(f"{p.get('data', '')}T{p.get('hora') or timezone.localtime().strftime('%H:%M')}")
        if not ids or not modelo or not quando:
            return redirect("/enviar/?erro=Escolha ao menos um contato, um modelo aprovado e a data.")
        quando = timezone.make_aware(quando) if timezone.is_naive(quando) else quando
        rec = p.get("recorrencia", "")
        if rec not in Aviso.Recorrencia.values:
            rec = ""
        leads = list(leads_visiveis(request.user).filter(pk__in=ids, nao_contatar=False))
        avisos.criar(leads, modelo, quando, rec, request.user)
        return redirect(f"/historico/?msg={len(leads)} aviso(s) programado(s)")


class HistoricoView(Base, TemplateView):
    template_name = "captacao/historico.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        p = self.request.GET
        hoje = timezone.localdate()
        try:
            ini = date.fromisoformat(p.get("de") or hoje.replace(day=1).isoformat())
            fim = date.fromisoformat(p.get("ate") or hoje.isoformat()) + timedelta(days=1)
        except ValueError:
            ini, fim = hoje.replace(day=1), hoje + timedelta(days=1)
        ver = p.get("ver", "")
        av = Aviso.objects.select_related("lead", "modelo").filter(enviar_em__date__gte=ini, enviar_em__date__lt=fim)
        av = av.filter(recorrencia__gt="", status="pendente") if ver == "recorrentes" else av.filter(arquivado=(ver == "arquivados"))
        linhas = [{"tipo": "aviso", "pk": a.pk, "status": a.status, "rotulo": a.get_status_display(), "texto": a.modelo.corpo, "erro": a.erro,
                   "lead": a.lead, "quando": a.enviar_em, "rec": a.get_recorrencia_display() if a.recorrencia else "", "origem": a.origem or "Envio manual"}
                  for a in av[:300]]
        if ver == "":
            envios = (CampanhaEnvio.objects.select_related("lead", "campanha", "modelo")
                      .filter(enviado_em__date__gte=ini, enviado_em__date__lt=fim) | CampanhaEnvio.objects.select_related("lead", "campanha", "modelo").filter(status="falhou"))
            linhas += [{"tipo": "campanha", "pk": e.pk, "status": e.status.replace("enviado", "enviado"), "rotulo": e.get_status_display(),
                        "texto": (e.modelo or e.campanha.modelo).corpo, "erro": e.erro, "lead": e.lead, "quando": e.enviado_em or e.enviar_em or e.campanha.criada_em,
                        "rec": "", "origem": f"Campanha: {e.campanha.nome}"} for e in envios.distinct()[:200]]
        linhas.sort(key=lambda l: l["quando"], reverse=True)
        todos = Aviso.objects.filter(enviar_em__date__gte=ini, enviar_em__date__lt=fim, arquivado=False)
        ctx.update(linhas=linhas[:300], ver=ver, de=ini.isoformat(), ate=(fim - timedelta(days=1)).isoformat(), msg=p.get("msg", ""),
                   n_pendente=todos.filter(status="pendente").count(), n_enviado=todos.filter(status="enviado").count() +
                   CampanhaEnvio.objects.filter(status="enviado", enviado_em__date__gte=ini, enviado_em__date__lt=fim).count(),
                   n_falhou=todos.filter(status="falhou").count() + CampanhaEnvio.objects.filter(status="falhou").count(),
                   n_recorrentes=Aviso.objects.filter(recorrencia__gt="", status="pendente").count(), gestor=eh_gestor(self.request.user))
        return ctx


class AvisoAcaoView(Base, View):
    def post(self, request, pk, acao):
        a = get_object_or_404(Aviso, pk=pk, lead__in=leads_visiveis(request.user))
        if acao == "cancelar" and a.status == "pendente":
            a.status = "cancelado"
            a.recorrencia = ""
        elif acao == "arquivar":
            a.arquivado = True
        elif acao == "reenviar" and a.status == "falhou":
            a.status, a.erro, a.enviar_em, a.recorrencia = "pendente", "", timezone.now(), ""
        else:
            return HttpResponse(status=400)
        a.save()
        return redirect(request.META.get("HTTP_REFERER") or "/historico/")


# ---------------------------------------------------------------- assistente de campanhas
TIPOS = [
    ("todos", "Todos os contatos", "Uma mensagem para toda a sua base, espalhada ao longo do dia."),
    ("regua", "Régua de disparos", "Marcou a etiqueta, entrou na régua: mensagens automáticas nos dias que você definir."),
    ("tag", "Por etiqueta", "Um envio para quem tem uma etiqueta específica."),
    ("etapa", "Por etapa do funil", "Para todos os leads que estão numa etapa do seu funil de vendas."),
    ("cadastro", "Por leads cadastrados", "Para quem chegou num período (máximo de 31 dias)."),
    ("aniversario", "Data de aniversário", "Mensagem automática no aniversário de cada contato."),
    ("avise", "Avise quando chegar", "Quem procurou um carro que não tínhamos é avisado quando ele entra no estoque."),
    ("item", "Carro do estoque", "Divulgue UM carro (preço e link) para quem veio de um canal específico."),
]


class CampanhaTiposView(Base, TemplateView):
    template_name = "captacao/campanha_tipos.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["tipos"] = TIPOS
        return ctx


class CampanhaNovaView(Base, View):
    def _ctx(self, request, tipo, erro=""):
        return {"tipo": tipo, "titulo": next(t[1] for t in TIPOS if t[0] == tipo), "desc": next(t[2] for t in TIPOS if t[0] == tipo), "erro": erro,
                "modelos": ModeloWhatsApp.objects.filter(status="APPROVED"), "etiquetas": Etiqueta.objects.all(), "etapas": funil.choices(),
                "origens": Lead.Origem.choices, "portais": Lead.Portal.choices, "carros": Veiculo.objects.filter(status__in=Veiculo.EM_LOJA).order_by("marca", "modelo"),
                "variaveis": modelos.VARIAVEIS, "passos": range(1, 7), "dados": request.POST}

    def get(self, request, tipo):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        if tipo not in dict((t[0], 1) for t in TIPOS):
            return HttpResponse(status=404)
        return render(request, "captacao/campanha_nova.html", self._ctx(request, tipo))

    def post(self, request, tipo):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        if tipo not in dict((t[0], 1) for t in TIPOS):
            return HttpResponse(status=404)
        p = request.POST
        nome = (p.get("nome") or "").strip()
        modelo = ModeloWhatsApp.objects.filter(pk=p.get("modelo", ""), status="APPROVED").first() if p.get("modelo", "").isdigit() else None
        erro, filtro, config = "", {}, {}
        if not nome:
            erro = "Dê um nome à campanha."
        elif tipo != "regua" and not modelo:
            erro = "Escolha um modelo aprovado."
        if not erro:
            if tipo == "todos":
                filtro = {"todos": True}
            elif tipo in ("tag", "regua"):
                tags = [i for i in p.getlist("etiquetas") if i.isdigit()]
                if not tags:
                    erro = "Escolha ao menos uma etiqueta."
                filtro, config["etiquetas"] = {"etiquetas": tags, "todos": True}, [int(i) for i in tags]
            elif tipo == "etapa":
                filtro = {"etapas": p.getlist("etapas")}
                if not filtro["etapas"]:
                    erro = "Escolha ao menos uma etapa."
            elif tipo == "cadastro":
                de, ate = parse_date(p.get("de", "")), parse_date(p.get("ate", ""))
                if not de or not ate or ate < de or (ate - de).days > 30:
                    erro = "Informe o período (no máximo 31 dias)."
                filtro = {"de": p.get("de"), "ate": p.get("ate"), "todos": True}
            elif tipo == "item":
                if not p.get("veiculo", "").isdigit():
                    erro = "Escolha o carro que será divulgado."
                filtro = {"origem": p.get("origem", ""), "portal": p.get("portal", "")}
                config["veiculo_id"] = p.get("veiculo")
        if tipo == "regua" and not erro:
            passos = []
            for i in range(1, 7):
                m = ModeloWhatsApp.objects.filter(pk=p.get(f"modelo_{i}", ""), status="APPROVED").first() if p.get(f"modelo_{i}", "").isdigit() else None
                if m:
                    passos.append({"dias": int(p.get(f"dias_{i}") or 0) if (p.get(f"dias_{i}") or "0").isdigit() else 0, "modelo": m.pk})
            if not passos:
                erro = "Monte ao menos um passo da régua, com um modelo aprovado."
            config["passos"] = passos
            modelo = modelo or ModeloWhatsApp.objects.get(pk=passos[0]["modelo"]) if passos else None
        if erro:
            return render(request, "captacao/campanha_nova.html", self._ctx(request, tipo, erro))
        c = campanhas.criar(nome, modelo, filtro, request.user, tipo=tipo, config=config, espalhar="espalhar" in p)
        return redirect(f"/campanhas/?criada={c.pk}")


# ---------------------------------------------------------------- agenda
class AgendaGestaoView(Base, TemplateView):
    """Profissionais e atividades."""
    template_name = "captacao/agenda_cadastros.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(profissionais=Profissional.objects.all(), atividades=Atividade.objects.all(), gestor=eh_gestor(self.request.user))
        return ctx


class AgendaCadastroView(Base, View):
    def post(self, request, acao, pk=0):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        p = request.POST
        if acao == "profissional" and (p.get("nome") or "").strip():
            Profissional.objects.create(nome=p["nome"].strip()[:80])
        elif acao == "atividade" and (p.get("nome") or "").strip():
            dur = int(p.get("duracao") or 30) if (p.get("duracao") or "30").isdigit() else 30
            Atividade.objects.create(nome=p["nome"].strip()[:80], duracao_min=max(5, min(dur, 600)))
        elif acao == "profissional_alternar":
            x = get_object_or_404(Profissional, pk=pk)
            x.ativo = not x.ativo
            x.save()
        elif acao == "atividade_alternar":
            x = get_object_or_404(Atividade, pk=pk)
            x.ativa = not x.ativa
            x.save()
        else:
            return HttpResponse(status=404)
        return redirect("captacao:agenda_cadastros")


class AgendamentoNovoView(Base, View):
    """Cria o agendamento: a atividade define a duração; o lead vai para 'Visita agendada' com o horário."""
    def post(self, request):
        p = request.POST
        lead = leads_visiveis(request.user).filter(pk=p.get("lead", "")).first() if p.get("lead", "").isdigit() else None
        inicio = parse_datetime(p.get("inicio", ""))
        if not lead or not inicio:
            return redirect("/agenda/?erro=Escolha o contato e o horário.")
        inicio = timezone.make_aware(inicio) if timezone.is_naive(inicio) else inicio
        ativ = Atividade.objects.filter(pk=p.get("atividade", "")).first() if p.get("atividade", "").isdigit() else None
        prof = Profissional.objects.filter(pk=p.get("profissional", "")).first() if p.get("profissional", "").isdigit() else None
        fim = inicio + timedelta(minutes=ativ.duracao_min if ativ else 30)
        if prof and Agendamento.objects.filter(profissional=prof, status__in=["agendado", "confirmado"], inicio__lt=fim, fim__gt=inicio).exists():
            return redirect(f"/agenda/?erro=Esse profissional já tem um compromisso nesse horário.")
        a = Agendamento.objects.create(lead=lead, profissional=prof, atividade=ativ, inicio=inicio, fim=fim,
                                       observacao=(p.get("observacao") or "")[:300], criado_por=request.user)
        try:
            if lead.etapa != Lead.Etapa.VISITA:
                services.mudar_etapa(lead, Lead.Etapa.VISITA, request.user, visita_em=inicio)
            else:
                lead.visita_em = inicio
                lead.save(update_fields=["visita_em", "atualizado_em"])
        except services.RegraErro:
            pass
        agendamentos.programar(a)
        agendamentos.rodar()            # a confirmação sai na hora (com a API conectada)
        services.adicionar_nota(lead, f"Agendado: {ativ.nome if ativ else 'visita'} em {timezone.localtime(inicio):%d/%m %H:%M}"
                                f"{' com ' + prof.nome if prof else ''}", request.user)
        return redirect("captacao:agenda")


class AgendamentoStatusView(Base, View):
    def post(self, request, pk, status):
        a = get_object_or_404(Agendamento, pk=pk, lead__in=leads_visiveis(request.user))
        if status not in Agendamento.Status.values:
            return HttpResponse(status=404)
        a.status = status
        a.save(update_fields=["status"])
        if status in ("cancelado", "faltou"):
            agendamentos.cancelar_pendentes(a, f"Agendamento {a.get_status_display().lower()}.")
        try:
            if status == "realizado" and a.lead.etapa == Lead.Etapa.VISITA:
                services.mudar_etapa(a.lead, Lead.Etapa.COMPARECEU, request.user)
            elif status == "faltou":
                services.adicionar_nota(a.lead, "Não compareceu ao agendamento", request.user)
        except services.RegraErro:
            pass
        return redirect(request.META.get("HTTP_REFERER") or "/agenda/")


# ---------------------------------------------------------------- conta e aniversário
class ContaView(Base, TemplateView):
    template_name = "captacao/conta.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(msg=self.request.GET.get("msg", ""), erro=self.request.GET.get("erro", ""))
        return ctx

    def post(self, request):
        u, p = request.user, request.POST
        u.first_name, u.last_name = (p.get("first_name") or "").strip()[:60], (p.get("last_name") or "").strip()[:60]
        u.email = (p.get("email") or "").strip()[:120]
        u.save(update_fields=["first_name", "last_name", "email"])
        return redirect("/conta/?msg=Dados salvos")


class AniversarioView(Base, View):
    def post(self, request, pk):
        lead = self.lead(request, pk)
        d = parse_date(request.POST.get("aniversario", ""))
        lead.aniversario = d
        lead.save(update_fields=["aniversario", "atualizado_em"])
        return HttpResponse(status=204)


class AgendaMensagensView(Base, TemplateView):
    """Configuração das mensagens automáticas do agendamento."""
    template_name = "captacao/agenda_mensagens.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(cfg=AgendaConfig.obter(), modelos=ModeloWhatsApp.objects.filter(status="APPROVED"), gestor=eh_gestor(self.request.user),
                   msg=self.request.GET.get("msg", ""), api=whatsapp.configurado(), variaveis=modelos.VARIAVEIS)
        return ctx

    def post(self, request):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        p, c = request.POST, AgendaConfig.obter()
        c.confirmar, c.lembrete_24h, c.lembrete_2h, c.pos_visita = ("confirmar" in p, "lembrete_24h" in p, "lembrete_2h" in p, "pos_visita" in p)
        c.txt_confirmacao = (p.get("txt_confirmacao") or c.txt_confirmacao).strip()[:900]
        c.txt_lembrete = (p.get("txt_lembrete") or c.txt_lembrete).strip()[:900]
        c.txt_pos = (p.get("txt_pos") or c.txt_pos).strip()[:900]
        for campo in ("modelo_confirmacao", "modelo_lembrete", "modelo_pos"):
            v = p.get(campo, "")
            setattr(c, campo, ModeloWhatsApp.objects.filter(pk=v, status="APPROVED").first() if v.isdigit() else None)
        c.save()
        return redirect("/agenda/mensagens/?msg=Mensagens do agendamento salvas")


class AgendamentoMensagemAcaoView(Base, View):
    """'reenviar' tenta pela API agora; o link do WhatsApp (manual) fica na própria agenda."""
    def post(self, request, pk, acao):
        m = get_object_or_404(AgendamentoMensagem, pk=pk, agendamento__lead__in=leads_visiveis(request.user))
        if acao == "reenviar" and m.status in ("falhou", "pendente"):
            m.status = "pendente"
            agendamentos.enviar(m)
        elif acao == "marcar_enviada":                        # a equipe mandou pelo celular
            m.status, m.enviada_em, m.erro = "enviada", timezone.now(), ""
            m.save(update_fields=["status", "enviada_em", "erro"])
        else:
            return HttpResponse(status=400)
        return redirect(request.META.get("HTTP_REFERER") or "/agenda/")
