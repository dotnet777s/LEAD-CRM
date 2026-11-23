from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, views_equipe, views_painel, views_publico as pub

app_name = "captacao"

urlpatterns = [
    # ---- público (captação) ----
    path("v/<int:pk>-<slug:slug>/", pub.CapturaView.as_view(), name="captura"),
    path("v/<int:pk>/", pub.CapturaView.as_view(), name="captura"),
    path("v/<int:pk>/inicio/", pub.IniciarView.as_view(), name="inicio"),
    path("v/<int:pk>/etapa/", pub.EtapaView.as_view(), name="etapa"),
    path("v/<int:pk>/consentir/", pub.ConsentirView.as_view(), name="consentir"),
    path("v/<int:pk>/obrigado/<uuid:lead_uuid>/", pub.obrigado, name="obrigado"),
    path("v/<int:pk>/contato/<uuid:lead_uuid>/", pub.ContatoView.as_view(), name="contato"),
    path("foto/<str:pk>/<int:largura>.webp", pub.foto, name="foto"),
    path("privacidade/", pub.privacidade, name="privacidade"),

    # ---- CRM (login) ----
    path("", views.KanbanView.as_view(), name="kanban"),
    path("quadro/", views.QuadroView.as_view(), name="quadro"),
    path("lead/novo/", views.NovoLeadView.as_view(), name="lead_novo"),
    path("lead/<int:pk>/", views.DetalheView.as_view(), name="lead"),
    path("lead/<int:pk>/mover/", views.MoverView.as_view(), name="mover"),
    path("lead/<int:pk>/mover-direto/", views.MoverDiretoView.as_view(), name="mover_direto"),
    path("lead/<int:pk>/nota/", views.NotaView.as_view(), name="nota"),
    path("lead/<int:pk>/consultor/", views.ConsultorView.as_view(), name="consultor"),
    path("lead/<int:pk>/retorno/", views.RetornoView.as_view(), name="retorno"),
    path("lead/<int:pk>/whatsapp/", views.WhatsAppView.as_view(), name="whatsapp"),
    path("lead/<int:pk>/excluir/", views.ExcluirView.as_view(), name="excluir"),
    path("painel/", views_painel.PainelView.as_view(), name="painel"),
    path("painel/formulario/", views_painel.FunilFormView.as_view(), name="funil_form"),
    path("painel/investimento/", views_painel.InvestimentoView.as_view(), name="investimento"),
    path("carros/", views_painel.CarrosView.as_view(), name="carros"),
    path("carros/<int:pk>/midias/", views_painel.MidiasView.as_view(), name="midias"),
    path("equipe/", views_equipe.EquipeView.as_view(), name="equipe"),
    path("equipe/<int:pk>/<slug:acao>/", views_equipe.EquipeAcaoView.as_view(), name="equipe_acao"),
    path("meta/", views.EventosMetaView.as_view(), name="eventos"),
    path("meta/<int:pk>/reenviar/", views.ReenviarEventoView.as_view(), name="reenviar"),

    path("entrar/", auth_views.LoginView.as_view(template_name="captacao/login.html",
                                                 redirect_authenticated_user=True), name="entrar"),
    path("sair/", auth_views.LogoutView.as_view(), name="sair"),
]
