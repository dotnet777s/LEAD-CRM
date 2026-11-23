from django import forms

from . import utils
from .models import Lead


class CapturaForm(forms.Form):
    nome = forms.CharField(max_length=120, min_length=2, error_messages={"required": "Informe seu nome."})
    telefone = forms.CharField(max_length=20, error_messages={"required": "Informe seu WhatsApp."})
    pagamento = forms.ChoiceField(choices=utils.PAGAMENTO, error_messages={"required": "Escolha uma opção."})
    entrada = forms.ChoiceField(choices=utils.ENTRADA, error_messages={"required": "Escolha uma opção."})
    troca = forms.ChoiceField(choices=utils.TROCA, error_messages={"required": "Escolha uma opção."})
    troca_carro = forms.CharField(max_length=100, required=False)
    prazo = forms.ChoiceField(choices=utils.PRAZO, error_messages={"required": "Escolha uma opção."})
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
        r = {k: d[k] for k in ("pagamento", "entrada", "troca", "prazo")}
        if d["troca"] == "sim":
            r["troca_carro"] = d["troca_carro"].strip()
        r["intencao"] = d.get("intencao") or "simular"
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
