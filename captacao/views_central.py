"""Telas de apoio da central: contatos, agenda de visitas, modelos e respostas rápidas, campanhas e conexão do WhatsApp."""
import csv
import io
import re
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from . import campanhas, followup, services, utils, whatsapp
from .models import Campanha, Historico, Lead, ModeloWhatsApp, RespostaRapida
from .views import Base, eh_gestor, leads_visiveis, trigger

PAGINA = 100


# ---------------------------------------------------------------- contatos
def _filtrar_contatos(user, p):
    qs = leads_visiveis(user)
    q = (p.get("q") or "").strip()
    if q:
        dig = utils.so_digitos(q)
        cond = Q(nome__icontains=q) | Q(email__icontains=q)
        if dig:
            cond |= Q(telefone__contains=dig)
        qs = qs.filter(cond)
    if p.get("origem") in Lead.Origem.values:
        qs = qs.filter(origem=p["origem"])
    if p.get("etapa") in Lead.Etapa.values:
        qs = qs.filter(etapa=p["etapa"])
    if p.get("nc") == "1":
        qs = qs.filter(nao_contatar=True)
    return qs.order_by("nome")


class ContatosView(Base, TemplateView):
    template_name = "captacao/contatos.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        p = self.request.GET
        qs = _filtrar_contatos(self.request.user, p)
        try:
            pag = max(1, int(p.get("p", 1)))
        except ValueError:
            pag = 1
        total = qs.count()
        ctx.update(contatos=qs[(pag - 1) * PAGINA: pag * PAGINA], total=total, pag=pag, tem_mais=total > pag * PAGINA,
                   q=p.get("q", ""), origem=p.get("origem", ""), etapa=p.get("etapa", ""), nc=p.get("nc", ""),
                   origens=Lead.Origem.choices, etapas=Lead.Etapa.choices, importado=p.get("importado"), ignorados=p.get("ignorados"),
                   gestor=eh_gestor(self.request.user))
        return ctx


class ContatosExportarView(Base, View):
    def get(self, request):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        r = HttpResponse(content_type="text/csv; charset=utf-8")
        r["Content-Disposition"] = 'attachment; filename="contatos-topcar.csv"'
        r.write("﻿")
        w = csv.writer(r, delimiter=";")
        w.writerow(["nome", "telefone", "email", "origem", "etapa", "carro", "criado_em"])
        for l in _filtrar_contatos(request.user, request.GET).iterator():
            w.writerow([l.nome, l.telefone, l.email, l.get_origem_display(), l.get_etapa_display(), l.carro_texto,
                        timezone.localtime(l.criado_em).strftime("%d/%m/%Y %H:%M")])
        return r


class ContatosImportarView(Base, View):
    """CSV com colunas nome e telefone (separador , ou ;). Telefones já cadastrados são ignorados."""
    def post(self, request):
        arq = request.FILES.get("arquivo")
        if not arq or arq.size > 2_000_000:
            return redirect("captacao:contatos")
        texto = arq.read().decode("utf-8-sig", errors="replace")
        sep = ";" if texto.splitlines()[0].count(";") >= texto.splitlines()[0].count(",") else ","
        novos = ign = 0
        for linha in csv.DictReader(io.StringIO(texto), delimiter=sep):
            l = {(k or "").strip().lower(): (v or "").strip() for k, v in linha.items()}
            tel = utils.normalizar_telefone(l.get("telefone") or l.get("celular") or l.get("whatsapp") or "")
            nome = l.get("nome") or l.get("name") or ""
            if not tel or not nome or Lead.objects.filter(telefone=tel).exists():
                ign += 1
                continue
            lead = Lead.objects.create(nome=nome[:120], telefone=tel, origem=Lead.Origem.OUTRO, email=(l.get("email") or "")[:254],
                                       etapa=Lead.Etapa.NOVO)
            Historico.objects.create(lead=lead, tipo=Historico.Tipo.CRIADO, usuario=request.user, texto="Importado por planilha")
            novos += 1
        return redirect(f"/contatos/?importado={novos}&ignorados={ign}")


# ---------------------------------------------------------------- agenda
class AgendaView(Base, TemplateView):
    template_name = "captacao/agenda.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        hoje = timezone.localdate()
        try:
            desloc = int(self.request.GET.get("s", 0))
        except ValueError:
            desloc = 0
        ini = hoje - timedelta(days=hoje.weekday()) + timedelta(weeks=desloc)
        fim = ini + timedelta(days=7)
        leads = (leads_visiveis(self.request.user).filter(visita_em__date__gte=ini, visita_em__date__lt=fim)
                 .exclude(etapa__in=[Lead.Etapa.PERDIDO]).order_by("visita_em"))
        dias = []
        for i in range(7):
            d = ini + timedelta(days=i)
            dias.append({"data": d, "hoje": d == hoje, "visitas": [l for l in leads if timezone.localtime(l.visita_em).date() == d]})
        atrasadas = (leads_visiveis(self.request.user).filter(etapa=Lead.Etapa.VISITA, visita_em__lt=timezone.now() - timedelta(hours=2))
                     .order_by("visita_em")[:30])
        ctx.update(dias=dias, ini=ini, fim=fim - timedelta(days=1), s=desloc, atrasadas=atrasadas,
                   total=sum(len(d["visitas"]) for d in dias))
        return ctx


class VisitaResultadoView(Base, View):
    """Depois da visita: 'compareceu' avança para Compareceu; 'faltou' volta o lead para atendimento com nota."""
    def post(self, request, pk, resultado):
        lead = self.lead(request, pk)
        try:
            if resultado == "compareceu":
                services.mudar_etapa(lead, Lead.Etapa.COMPARECEU, request.user)
            elif resultado == "faltou":
                services.adicionar_nota(lead, f"Não compareceu à visita de {timezone.localtime(lead.visita_em):%d/%m %H:%M}"
                                        if lead.visita_em else "Não compareceu à visita", request.user)
                services.mudar_etapa(lead, Lead.Etapa.ATENDIMENTO, request.user)
                followup.iniciar(lead)
            else:
                return HttpResponse(status=404)
        except services.RegraErro as e:
            return HttpResponse(str(e), status=400)
        return redirect(request.META.get("HTTP_REFERER") or "captacao:agenda")


# ---------------------------------------------------------------- modelos e respostas rápidas
class ModelosView(Base, TemplateView):
    template_name = "captacao/modelos.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(modelos=ModeloWhatsApp.objects.all(), rapidas=RespostaRapida.objects.all(), api=whatsapp.configurado(),
                   tem_waba=bool(settings.WHATSAPP_WABA_ID), gestor=eh_gestor(self.request.user),
                   msg=self.request.GET.get("msg", ""), erro=self.request.GET.get("erro", ""))
        return ctx


class ModelosAcaoView(Base, View):
    def post(self, request, acao):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        p = request.POST
        try:
            if acao == "sincronizar":
                n = whatsapp.sincronizar_modelos()
                return redirect(f"/modelos/?msg={n} modelo(s) atualizado(s)")
            if acao == "criar":
                nome = re.sub(r"[^a-z0-9_]", "_", utils.sem_acento(p.get("nome", "")).lower().strip())[:100]
                corpo = (p.get("corpo") or "").strip()
                if not nome or not corpo or len(corpo) > 1024:
                    raise whatsapp.WhatsAppErro("Informe o nome e o texto (até 1024 caracteres).")
                exemplos = [e.strip() for e in (p.get("exemplos") or "").split(",") if e.strip()]
                nvars = len(set(re.findall(r"\{\{(\d+)\}\}", corpo)))
                if nvars and len(exemplos) < nvars:
                    raise whatsapp.WhatsAppErro(f"O texto tem {nvars} variável(is): informe {nvars} exemplo(s), separados por vírgula.")
                whatsapp.criar_modelo(nome, p.get("categoria") if p.get("categoria") in ("UTILITY", "MARKETING") else "UTILITY",
                                      corpo, exemplos[:nvars])
                return redirect("/modelos/?msg=Modelo enviado para aprovação da Meta")
            if acao == "rapida":
                titulo, texto = (p.get("titulo") or "").strip()[:60], (p.get("texto") or "").strip()[:1500]
                if titulo and texto:
                    RespostaRapida.objects.create(titulo=titulo, texto=texto, ordem=RespostaRapida.objects.count())
                return redirect("/modelos/?msg=Resposta rápida salva")
        except whatsapp.WhatsAppErro as e:
            return redirect(f"/modelos/?erro={e}")
        return HttpResponse(status=404)


class RapidaExcluirView(Base, View):
    def post(self, request, pk):
        if eh_gestor(request.user):
            RespostaRapida.objects.filter(pk=pk).delete()
        return redirect("captacao:modelos")


# ---------------------------------------------------------------- campanhas
class CampanhasView(Base, TemplateView):
    template_name = "captacao/campanhas.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        lista = list(Campanha.objects.select_related("modelo")[:50])
        for c in lista:
            c.n = campanhas.numeros(c)
        ctx.update(campanhas=lista, modelos=ModeloWhatsApp.objects.filter(status="APPROVED"), api=whatsapp.configurado(),
                   etapas=Lead.Etapa.choices, origens=Lead.Origem.choices, portais=Lead.Portal.choices, gestor=eh_gestor(self.request.user),
                   enviados_24h=campanhas.enviados_24h(), limite=settings.WHATSAPP_LIMITE_DIARIO, erro=self.request.GET.get("erro", ""))
        return ctx


def _filtro_do_form(p):
    return {"etapas": p.getlist("etapas"), "origem": p.get("origem", ""), "portal": p.get("portal", ""),
            "dias_parado": int(p.get("dias_parado") or 0) if (p.get("dias_parado") or "0").isdigit() else 0}


class CampanhaPreviaView(Base, View):
    """Quantas pessoas receberiam (atualiza ao mexer nos filtros)."""
    def get(self, request):
        n = campanhas.publico(_filtro_do_form(request.GET)).count()
        return HttpResponse(f"<b>{n}</b> pessoa(s) receberiam esta campanha.")


class CampanhaCriarView(Base, View):
    def post(self, request):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        p = request.POST
        modelo = ModeloWhatsApp.objects.filter(pk=p.get("modelo", ""), status="APPROVED").first() if p.get("modelo", "").isdigit() else None
        nome = (p.get("nome") or "").strip()
        if not modelo or not nome:
            return redirect("/campanhas/?erro=Informe o nome e escolha um modelo aprovado.")
        c = campanhas.criar(nome, modelo, _filtro_do_form(p), request.user)
        return redirect(f"/campanhas/?criada={c.pk}")


class CampanhaAcaoView(Base, View):
    def post(self, request, pk, acao):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        c = get_object_or_404(Campanha, pk=pk)
        try:
            if acao in ("iniciar", "retomar"):
                campanhas.iniciar(c)
            elif acao == "pausar":
                campanhas.pausar(c)
            else:
                return HttpResponse(status=404)
        except whatsapp.WhatsAppErro as e:
            return redirect(f"/campanhas/?erro={e}")
        return redirect("captacao:campanhas")


# ---------------------------------------------------------------- conexão do WhatsApp
class ConexaoView(Base, TemplateView):
    template_name = "captacao/whatsapp_conexao.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        info, erro = None, ""
        if whatsapp.configurado():
            try:
                info = whatsapp.info_numero()
            except whatsapp.WhatsAppErro as e:
                erro = str(e)
        ctx.update(api=whatsapp.configurado(), info=info, erro=erro, gestor=eh_gestor(self.request.user),
                   webhook=f"{settings.SITE_URL}/whatsapp/webhook/",
                   passos=[("Número e token no .env", bool(settings.WHATSAPP_PHONE_ID and settings.WHATSAPP_TOKEN)),
                           ("ID da conta (WABA) no .env", bool(settings.WHATSAPP_WABA_ID)),
                           ("Segredo do app (assinatura do webhook)", bool(settings.WHATSAPP_APP_SECRET)),
                           ("Token de verificação do webhook", bool(settings.WHATSAPP_VERIFY_TOKEN))])
        return ctx
