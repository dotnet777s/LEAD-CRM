from django.contrib.auth import get_user_model

from .test_central import Logado, lead, msg


class EstruturaTests(Logado):
    def test_menu_principal_enxuto_com_todas_as_secoes(self):
        html = self.client.get("/").content.decode()
        for rotulo in ("Conversas", "Funil", "Agenda", "Contatos", "Campanhas", "Carros", "Resultados", "Configurações"):
            self.assertIn(f"<span>{rotulo}</span>", html)
        self.assertEqual(html.count('class="bottombar"'), 1)              # menu inferior do celular
        for trecho in ('id="tema"', 'id="recolher"', 'id="mais"', 'class="busca-g"', 'data-theme'):
            self.assertIn(trecho, html)

    def test_tema_escuro_e_o_padrao_e_pode_ser_trocado(self):
        html = self.client.get("/").content.decode()
        self.assertIn("if(!t)t='dark'", html)
        self.assertIn("localStorage.setItem('tema'", open("captacao/static/captacao/crm.js", encoding="utf-8").read())

    def test_cada_secao_mostra_suas_abas(self):
        casos = {"/": ("Quadro", "Follow-up", "Portais"), "/agenda/": ("Profissionais e atividades", "Mensagens ao cliente"),
                 "/campanhas/": ("Enviar", "Histórico de envios"), "/visao-geral/": ("Anúncios (Meta)", "Eventos Meta"),
                 "/configuracoes/": ("WhatsApp", "Modelos", "Etapas do funil", "Etiquetas e regras", "Equipe")}
        for url, abas in casos.items():
            html = self.client.get(url).content.decode()
            self.assertIn('class="tabs-wrap"', html, url)
            for a in abas:
                self.assertIn(f">{a}", html, f"{url} sem a aba {a}")

    def test_aba_ativa_marcada(self):
        self.assertIn('class="on">Follow-up', self.client.get("/followup/").content.decode().replace("\n", ""))

    def test_central_de_configuracoes(self):
        html = self.client.get("/configuracoes/").content.decode()
        for cartao in ("WhatsApp", "Modelos e respostas rápidas", "Etapas do funil", "Etiquetas e regras", "Follow-up", "Estoque (AutoCerto)", "Equipe", "Minha conta"):
            self.assertIn(cartao, html)

    def test_consultor_nao_ve_equipe_nas_configuracoes(self):
        self.client.force_login(get_user_model().objects.create_user("vend", password="x"))
        html = self.client.get("/configuracoes/").content.decode()
        self.assertNotIn("Consultores, gestores", html)

    def test_busca_global_leva_aos_contatos(self):
        lead(nome="Zé da Silva")
        self.assertIn('action="/contatos/"', self.client.get("/").content.decode())
        self.assertContains(self.client.get("/contatos/?q=zé"), "Zé da Silva")

    def test_contatos_mostram_etapa_colorida_e_etiquetas(self):
        from captacao.models import Etiqueta
        l = lead()
        l.etiquetas.add(Etiqueta.objects.create(nome="VIP", cor="#ff0000"))
        html = self.client.get("/contatos/").content.decode()
        self.assertIn("--c:#64748b", html)         # cor da etapa
        self.assertIn("VIP", html)

    def test_titulo_da_pagina_na_barra_superior(self):
        for url, titulo in (("/", "Funil"), ("/contatos/", "Contatos"), ("/painel/", "Resultados"), ("/modelos/", "Modelos")):
            self.assertIn(f'<span class="tt">{titulo}</span>', self.client.get(url).content.decode(), url)
