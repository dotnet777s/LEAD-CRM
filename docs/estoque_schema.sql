-- Contrato do estoque (dono: ROUTE). O ADCRM só lê estas tabelas.
-- Rode com o usuário dono do banco (neondb_owner). Idempotente.

CREATE TABLE IF NOT EXISTS public.estoque_veiculo (
    id            bigserial PRIMARY KEY,
    marca         varchar(60)   NOT NULL,
    modelo        varchar(80)   NOT NULL,
    versao        varchar(80)   NOT NULL DEFAULT '',
    ano_modelo    integer       NOT NULL,
    placa         varchar(8)    NOT NULL,
    cor           varchar(30)   NOT NULL DEFAULT '',
    km            integer       NOT NULL DEFAULT 0,
    valor_venda   numeric(10,2) NOT NULL,
    status        varchar(20)   NOT NULL CHECK (status IN ('preparacao','loja_recife','loja_olinda','vendido')),
    possui_fotos  boolean       NOT NULL DEFAULT false,
    data_venda    date,
    criado_em     timestamptz   NOT NULL DEFAULT now(),
    atualizado_em timestamptz   NOT NULL DEFAULT now(),
    ficha         jsonb         NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS estoque_veiculo_status_idx ON public.estoque_veiculo (status);

CREATE TABLE IF NOT EXISTS public.estoque_takeveiculo (
    id          bigserial PRIMARY KEY,
    veiculo_id  bigint       NOT NULL,
    arquivo     varchar(500) NOT NULL,
    miniatura   varchar(500) NOT NULL DEFAULT '',
    ordem       smallint     NOT NULL DEFAULT 0,
    largura     integer      NOT NULL DEFAULT 0,
    altura      integer      NOT NULL DEFAULT 0,
    categoria   varchar(40)  NOT NULL DEFAULT '',
    destaque    varchar(80)  NOT NULL DEFAULT '',
    foto        boolean      NOT NULL DEFAULT true,
    ativo       boolean      NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS estoque_take_veiculo_idx ON public.estoque_takeveiculo (veiculo_id, ordem);

-- Vídeos ficam na máquina do ROUTE; aqui só os metadados.
CREATE TABLE IF NOT EXISTS public.estoque_videoveiculo (
    id             bigserial PRIMARY KEY,
    veiculo_id     bigint       NOT NULL,
    nome           varchar(200) NOT NULL,
    caminho_local  varchar(500) NOT NULL,
    tamanho_bytes  bigint       NOT NULL DEFAULT 0,
    duracao_s      integer,
    criado_em      timestamptz  NOT NULL DEFAULT now(),
    ativo          boolean      NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS estoque_video_veiculo_idx ON public.estoque_videoveiculo (veiculo_id);

-- Acesso do ADCRM: usuário próprio, esquema próprio (crm) e SÓ LEITURA no estoque.
-- Troque SENHA_FORTE antes de rodar.
-- CREATE ROLE crm_app LOGIN PASSWORD 'SENHA_FORTE';
-- CREATE SCHEMA IF NOT EXISTS crm AUTHORIZATION crm_app;
-- ALTER ROLE crm_app SET search_path = crm, public;
-- GRANT USAGE ON SCHEMA public TO crm_app;
-- GRANT SELECT ON public.estoque_veiculo, public.estoque_takeveiculo, public.estoque_videoveiculo TO crm_app;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO crm_app;
