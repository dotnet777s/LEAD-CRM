from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse
from django.shortcuts import resolve_url

# Públicos: página de captura do carro, obrigado, privacidade, fotos, estáticos e admin (login próprio).
PUBLICOS = ("/v/", "/catalogo/", "/whatsapp/webhook/", "/privacidade/", "/foto/", "/static/", "/admin/", "/robots.txt", "/favicon.ico")


class LoginObrigatorioMiddleware:
    """O CRM inteiro é privado; só a captação (página do carro) e a política de privacidade são públicas."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not self._livre(request.path_info):
            login = resolve_url(settings.LOGIN_URL)
            if request.headers.get("HX-Request"):
                resposta = HttpResponse(status=204)
                resposta["HX-Redirect"] = login
                return resposta
            return redirect_to_login(request.get_full_path(), login)
        return self.get_response(request)

    @staticmethod
    def _livre(caminho):
        return caminho == resolve_url(settings.LOGIN_URL) or caminho.startswith(PUBLICOS)


def contexto_crm(request):
    """Disponibiliza `eh_gestor` em todos os templates (menu Equipe)."""
    from .views import eh_gestor
    u = getattr(request, "user", None)
    return {"eh_gestor": bool(u and u.is_authenticated and eh_gestor(u))}
