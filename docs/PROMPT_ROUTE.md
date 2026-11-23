# Ajustes pedidos ao ROUTE (para o ADCRM consumir o estoque)

O ADCRM (CRM de captação, hospedado num servidor online) lê o estoque diretamente do banco Neon do ROUTE, **somente leitura**.
Hoje ele já lê `veiculo`, `veiculo_foto` e `veiculo_foto_arquivo`. Este documento lista o que o ROUTE precisa **manter e ajustar**.
Não renomeie nem remova colunas sem avisar: o ADCRM depende delas.

## 1. Contrato atual (manter)
`veiculo`: `id` (texto), `topcrm_id` (inteiro), `placa`, `marca`, `modelo`, `versao`, `ano`, `km`, `preco`, `cor`, `cambio`,
`combustivel`, `motor`, `opcionais` (jsonb), `itens_serie` (jsonb), `status` (`disponivel` | `preparacao` | `vendido`),
`unidade` (`Olinda` | `Recife`), `data_venda`, `total_fotos`, `capa_url`, `atualizado_em`.
`veiculo_foto`: `id`, `veiculo_id` (= `veiculo.id`), `ordem`, `capa`, `largura`, `altura`, `bytes`, `sha256`, `url`, `url_miniatura`, `criado_em`.
`veiculo_foto_arquivo`: `foto_id`, `jpeg` (bytea), `miniatura` (bytea).

Regras que o ADCRM assume:
- A página pública de um carro só existe com `status = 'disponivel'` **e** `total_fotos > 0`. Vendido, em preparação ou sem foto: a página fica indisponível automaticamente.
- `total_fotos` precisa ser recalculado a cada upload ou remoção de foto, na mesma transação.
- Mudar de loja é só trocar `unidade`. Os dois estoques são vendidos juntos.
- Vender = `status = 'vendido'` + `data_venda`. **Não apague o registro**: o CRM guarda o histórico dos leads por carro.

## 2. `topcrm_id` obrigatório e único em todo carro (importante)
O ADCRM identifica o carro pelo `topcrm_id` (é o número que vai nos links dos anúncios). Hoje os 132 carros têm. **Todo carro novo cadastrado no ROUTE
precisa receber um `topcrm_id` inteiro, único e que nunca é reutilizado** (use uma sequence do Postgres, começando acima do maior valor atual).
Carro sem `topcrm_id` não aparece no CRM. Crie também uma constraint `UNIQUE (topcrm_id)` e `NOT NULL`.

## 3. Espaço do banco: fotos no Neon vão estourar o plano grátis
As fotos estão guardadas dentro do banco (~190 KB cada). O Neon grátis tem 0,5 GB; com cerca de 15 fotos por carro e 130 carros isso passa de 350 MB,
e o banco ainda guarda o resto. Proposta:
- Mover as imagens para um bucket S3 privado e gratuito (**Cloudflare R2**, 10 GB, sem custo de saída) ou Backblaze B2 (10 GB).
- Gravar o endereço em `veiculo_foto.url` (e `url_miniatura`) e deixar `veiculo_foto_arquivo` vazia ou removida. **O ADCRM já aceita os dois modos**: se `url` estiver
  preenchida e for pública (https), ele baixa dali; senão, usa os bytes de `veiculo_foto_arquivo`.
- Para o ADCRM conseguir baixar, a `url` precisa ser acessível sem credencial (URL pública do R2 ou link assinado de longa duração). Se preferir bucket privado, avise: o ADCRM passa a assinar os links.
- No upload: limite de 2000 px no lado maior, WebP/JPEG qualidade ~80, remover EXIF/GPS, nome único.
- Escreva um comando de migração idempotente das fotos já existentes e um relatório do que foi migrado.

## 4. Vídeos: ficam na máquina do ROUTE
Os vídeos não vão para a nuvem. O ADCRM, que está num servidor, não enxerga o disco do ROUTE, então por ora só mostra que eles existem.
Registre os metadados numa tabela `veiculo_video`: `id`, `veiculo_id`, `nome`, `duracao_s`, `tamanho_bytes`, `criado_em`, `ativo`
(sem expor o caminho do disco). A reprodução remota fica para uma segunda etapa (ex.: túnel do ROUTE).

## 5. Usuário somente leitura para o ADCRM
No Neon, crie o papel `crm_app` com `SELECT` apenas em `veiculo`, `veiculo_foto`, `veiculo_foto_arquivo`, `veiculo_video`, e um esquema `crm`
(`CREATE SCHEMA crm AUTHORIZATION crm_app; ALTER ROLE crm_app SET search_path = crm, public;`). As tabelas do CRM ficam no esquema `crm`;
o ROUTE nunca deve ler, criar ou apagar nada nele.

## 6. Entrega
- Teste automatizado para: cadastrar, editar, vender, trocar de unidade, subir/remover foto e o recálculo de `total_fotos`.
- Comando de verificação: acusa carro disponível sem foto, foto sem arquivo e `topcrm_id` nulo ou duplicado.
- README atualizado (variáveis do `.env`, bucket, backup e restauração).
