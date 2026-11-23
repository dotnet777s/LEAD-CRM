from django import forms

from . import utils
from .models import Lead


class CapturaForm(forms.Form):
    nome = forms.CharField(max_length=120, min_length=2, error_messages={"required": "Informe seu nome."})
    telefone = forms.CharField(max_length=20, error_messages={"required": "Informe seu WhatsApp."})
    pagamento = forms.ChoiceField(choices=utils.PAGAMENTO, error_messages={"required": "Escolha uma opção."})
    prazo = forms.ChoiceField(choices=utils.PRAZO, error_messages={"required": "Escolha uma opção."})
    entrada = forms.ChoiceField(choices=utils.ENTRADA, required=False)         # opcional: quem não responde fala no WhatsApp
    troca = forms.ChoiceField(choices=utils.TROCA, required=False)             # opcional
    troca_carro = forms.CharField(max_length=100, required=False)
    intencao = forms.ChoiceField(choices=utils.INTENCAO, required=False)     # botão que o cliente escolheu na página
    lgpd = forms.BooleanField(error_messages={"required": "É preciso aceitar para continuar."})
    website = forms.CharField(required=False)   # honeypot: humanos não preenchem

    def clean_nome(self):
        return " ".join(self.cleaned_data["nome"].split())

    def clean_telefone(self):
        tel = utils.normalizar_telefone(self.cleaned_data["telefone"])
        if not tel:
            raise forms.ValidationError("WhatsApp inválido. Use DDD + 9 dígitos, ex.: (81) 99999-9999.")
        return tel

    def clean(self):
        d = super().clean()
        if d.get("troca") == "sim" and not (d.get("troca_carro") or "").strip():
            self.add_error("troca_carro", "Informe o modelo e o ano do carro ou da moto.")
        return d

    def respostas(self):
        d = self.cleaned_data
        r = {k: d[k] for k in ("pagamento", "prazo")}
        for k in ("entrada", "troca"):
            if d.get(k):
                r[k] = d[k]
        if d.get("troca") == "sim":
            r["troca_carro"] = d["troca_carro"].strip()
        r["intencao"] = d.get("intencao") or "simular"
        return r


class ContatoInicialForm(forms.Form):
    """Passo do contato: o cliente já respondeu pagamento e prazo. O lead fica salvo para a equipe antes do último passo."""
    nome = forms.CharField(max_length=120, min_length=2)
    telefone = forms.CharField(max_length=20)
    pagamento = forms.ChoiceField(choices=utils.PAGAMENTO)
    prazo = forms.ChoiceField(choices=utils.PRAZO)
    lgpd = forms.BooleanField()
    intencao = forms.ChoiceField(choices=utils.INTENCAO, required=False)
    website = forms.CharField(required=False)   # honeypot

    def clean_nome(self):
        return " ".join(self.cleaned_data["nome"].split())

    def clean_telefone(self):
        tel = utils.normalizar_telefone(self.cleaned_data["telefone"])
        if not tel:
            raise forms.ValidationError("WhatsApp inválido.")
        return tel

    def respostas_parciais(self, variante):
        d = self.cleaned_data
        r = {"parcial": True, "pagamento": d["pagamento"], "prazo": d["prazo"], "intencao": d.get("intencao") or "simular",
             "variante": variante}
        return r


class LeadManualForm(forms.Form):
    nome = forms.CharField(max_length=120)
    telefone = forms.CharField(max_length=20)
    origem = forms.ChoiceField(choices=[c for c in Lead.Origem.choices if c[0] != Lead.Origem.META_LP])
    veiculo = forms.IntegerField(required=False)
    observacao = forms.CharField(required=False, max_length=500)

    def clean_telefone(self):
        tel = utils.normalizar_telefone(self.cleaned_data["telefone"])
        if not tel:
            raise forms.ValidationError("WhatsApp inválido (DDD + número).")
        return tel
