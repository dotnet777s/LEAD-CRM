from django.contrib import admin

from .models import EventoMeta, Historico, Interacao, Investimento, Lead, PortalMensagem


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("nome", "telefone", "etapa", "classe", "origem", "portal", "vendedor", "criado_em")
    list_filter = ("etapa", "classe", "origem", "portal")
    search_fields = ("nome", "telefone")


admin.site.register([EventoMeta, Historico, Interacao, Investimento])


@admin.register(PortalMensagem)
class PortalMensagemAdmin(admin.ModelAdmin):
    list_display = ("criado_em", "portal", "status", "assunto", "lead")
    list_filter = ("status", "portal")
    search_fields = ("assunto", "remetente", "corpo")
