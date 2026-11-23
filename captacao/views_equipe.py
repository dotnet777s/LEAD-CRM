"""Equipe: o gestor cria e gerencia os consultores (sem precisar do /admin/)."""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View

from .models import TelegramVinculo
from .views import eh_gestor

User = get_user_model()


class NovoUsuarioForm(forms.Form):
    nome = forms.CharField(max_length=60, label="Nome")
    usuario = forms.CharField(max_length=40, label="Usuário (para entrar)")
    senha = forms.CharField(min_length=8, widget=forms.PasswordInput(render_value=False), label="Senha inicial")
    gestor = forms.BooleanField(required=False, label="Gestor (vê todos os leads)")

    def clean_usuario(self):
        u = self.cleaned_data["usuario"].strip().lower().replace(" ", "")
        if User.objects.filter(username__iexact=u).exists():
            raise ValidationError("Este usuário já existe.")
        return u

    def clean(self):
        d = super().clean()
        if d.get("senha"):
            try:
                validate_password(d["senha"])
            except ValidationError as e:
                self.add_error("senha", e)
        return d


class GestorView(View):
    def dispatch(self, request, *a, **k):
        if request.user.is_authenticated and not eh_gestor(request.user):
            return HttpResponseForbidden("Somente gestores.")
        return super().dispatch(request, *a, **k)


class EquipeView(GestorView):
    def contexto(self, request, form=None):
        usuarios = (User.objects.annotate(abertos=Count("leads_crm", filter=~Q(leads_crm__etapa__in=["vendido", "perdido"])))
                    .order_by("-is_active", "first_name", "username"))
        return {"usuarios": usuarios, "form": form or NovoUsuarioForm(),
                "gestores": {u.pk for u in usuarios if eh_gestor(u)},
                "telegram": set(TelegramVinculo.objects.filter(chat_id__isnull=False).values_list("user_id", flat=True))}

    def get(self, request):
        return render(request, "captacao/equipe.html", self.contexto(request))

    def post(self, request):
        form = NovoUsuarioForm(request.POST)
        if not form.is_valid():
            return render(request, "captacao/equipe.html", self.contexto(request, form), status=400)
        d = form.cleaned_data
        u = User.objects.create_user(d["usuario"], password=d["senha"], first_name=d["nome"].strip())
        if d["gestor"]:
            u.groups.add(Group.objects.get_or_create(name="Gestor")[0])
        messages.success(request, f"{u.first_name} criado. Usuário: {u.username}")
        return HttpResponseRedirect(reverse("captacao:equipe"))


class EquipeAcaoView(GestorView):
    def post(self, request, pk, acao):
        u = get_object_or_404(User, pk=pk)
        if u == request.user and acao == "ativo":
            messages.error(request, "Você não pode desativar o seu próprio usuário.")
        elif acao == "ativo":
            u.is_active = not u.is_active
            u.save(update_fields=["is_active"])
            messages.success(request, f"{u.first_name or u.username}: {'ativado' if u.is_active else 'desativado'}.")
        elif acao == "telegram":
            from django.conf import settings as cfg
            from . import telegram
            if not telegram.configurado():
                messages.error(request, "Defina TELEGRAM_BOT_TOKEN no .env para usar o bot.")
            else:
                codigo = telegram.gerar_codigo(u)
                messages.success(request, f"Código de {u.first_name or u.username}: {codigo} (vale {telegram.VALIDADE_CODIGO_MIN} min). "
                                          f"Peça para abrir @{cfg.TELEGRAM_BOT_USERNAME} no Telegram e enviar: /start {codigo}")
        elif acao == "telegram-sair":
            from .models import TelegramVinculo
            TelegramVinculo.objects.filter(user=u).update(chat_id=None, codigo="", vinculado_em=None)
            messages.success(request, f"Telegram de {u.first_name or u.username} desvinculado.")
        elif acao == "senha":
            nova = request.POST.get("senha", "")
            try:
                validate_password(nova, u)
                u.set_password(nova)
                u.save(update_fields=["password"])
                messages.success(request, f"Senha de {u.first_name or u.username} redefinida.")
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
        return HttpResponseRedirect(reverse("captacao:equipe"))
