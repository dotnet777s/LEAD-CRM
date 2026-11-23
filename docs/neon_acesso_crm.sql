-- Rode no Neon (SQL Editor), logado como o dono do banco (neondb_owner).
-- Troque TROQUE_ESTA_SENHA por uma senha forte (só letras e números evita problemas na URL).

CREATE ROLE crm_app LOGIN PASSWORD 'TROQUE_ESTA_SENHA';

-- Esquema próprio do CRM (leads, usuários, filas). O ROUTE não mexe nele.
CREATE SCHEMA IF NOT EXISTS crm AUTHORIZATION crm_app;
ALTER ROLE crm_app SET search_path = crm, public;

-- Estoque do ROUTE: SOMENTE LEITURA.
GRANT USAGE ON SCHEMA public TO crm_app;
GRANT SELECT ON public.veiculo, public.veiculo_foto, public.veiculo_foto_arquivo TO crm_app;
-- Tabelas que o ROUTE criar no futuro (ex.: veiculo_video) já nascem legíveis:
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO crm_app;
