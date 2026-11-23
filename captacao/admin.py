from django.contrib import admin

from .models import EventoMeta, Historico, Interacao, Investimento, Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("nome", "telefone", "etapa", "classe", "origem", "vendedor", "criado_em")
    list_filter = ("etapa", "classe", "origem")
    search_fields = ("nome", "telefone")


admin.site.register([EventoMeta, Historico, Interacao, Investimento])
