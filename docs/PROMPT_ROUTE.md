# Prompt para o projeto ROUTE (banco do estoque + imagens na nuvem)

Cole o texto abaixo no Claude Code, dentro da pasta do projeto ROUTE.

---

Você vai evoluir o ROUTE, o sistema que gerencia o estoque da TopCar Veículos (Recife e Olinda), para que ele passe a ser
o **dono do banco de estoque online** e das **fotos na nuvem**. Esse banco será lido por um segundo sistema, o ADCRM
(CRM de captação de leads, hospedado num servidor online, com páginas públicas por carro para anúncios). O ROUTE continua rodando no localhost da máquina da empresa.

## 0. Antes de escrever código
Leia o ROUTE inteiro e me entregue um relatório curto:
1. Como o estoque é guardado hoje (banco, tabelas, campos), como as fotos e os vídeos são guardados e enviados, e como é o fluxo de cadastrar, editar, vender e remover um carro.
2. O que já atende o contrato abaixo e o que diverge.
3. O plano de mudanças, mínimo e sem reescrever o que já funciona.
Só comece a implementar depois de eu aprovar o plano. Não quebre nenhum fluxo existente.

## 1. Banco de dados online (grátis)
- O banco **já existe e está vazio**: Neon, projeto `ep-silent-mountain-b6qlbsef`, database `neondb` (região sa-east-1). Crie as tabelas pelo script `docs/estoque_schema.sql` do repositório do ADCRM (https://github.com/dotnet777s/LEAD-CRM) ou pelas suas migrations, desde que o resultado seja idêntico ao contrato da seção 2.
- O ADCRM usará o **mesmo banco**, mas num esquema separado chamado `crm`. O ROUTE nunca deve ler, criar ou apagar nada no esquema `crm`; suas tabelas ficam no `public`.
- PostgreSQL gerenciado, plano gratuito: **Neon** (0,5 GB por projeto, mais que suficiente para o estoque; usar a URL com `-pooler`). Alternativa aceitável: Supabase.
- O ROUTE passa a ser o **único escritor** do estoque. Configure por `DATABASE_URL` no `.env`, nunca no código.
- Crie no Postgres um papel **somente leitura** (`crm_leitura`, apenas `SELECT` nas tabelas `estoque_*`) e me entregue a URL dele: é a que o ADCRM vai usar. O ADCRM nunca escreve no estoque.
- Backup: exporte um dump diário (`pg_dump`) para um local seguro e documente como restaurar.

## 2. Contrato das tabelas (o ADCRM lê exatamente isto; não renomeie colunas)

`estoque_veiculo`
| coluna | tipo | observação |
|---|---|---|
| id | bigint PK | |
| marca, modelo | varchar | |
| versao | varchar | pode ser vazio |
| ano_modelo | int | aceitamos 2024, 24 ou 2324 (fab+modelo); o CRM normaliza |
| placa | varchar(8) | |
| cor | varchar | |
| km | int | |
| valor_venda | numeric(10,2) | preço anunciado |
| status | varchar | **preparacao**, **loja_recife**, **loja_olinda** ou **vendido** |
| possui_fotos | boolean | ver regra 3 |
| data_venda | date null | preenchida ao vender |
| criado_em, atualizado_em | timestamptz | `atualizado_em` muda a cada alteração do carro ou das mídias |
| ficha | jsonb | ficha técnica/opcionais; preserve o que já existe |

`estoque_takeveiculo` (fotos)
| coluna | tipo | observação |
|---|---|---|
| id | bigint PK | |
| veiculo_id | bigint | referência ao carro (sem exigir FK rígida) |
| arquivo | varchar | nome do arquivo no bucket, sem a pasta |
| miniatura | varchar | opcional |
| ordem | smallint | ordem de exibição; a primeira é a capa |
| largura, altura | int | em pixels |
| categoria | varchar | ex.: frente, traseira, interior, motor |
| destaque | varchar | texto livre; `capa` marca a foto principal |
| foto | boolean | `true` para foto |
| ativo | boolean | `false` = removida (não apague na hora) |

Regras de negócio que o ROUTE precisa garantir, sempre dentro de **uma transação**:
1. **Cadastrar carro:** insere em `estoque_veiculo` com o status inicial.
2. **Editar carro:** atualiza os campos e `atualizado_em`.
3. **Fotos e `possui_fotos`:** `possui_fotos = true` somente se existir pelo menos 1 foto ativa; ao remover a última, volta para `false`.
4. **Vender:** `status = 'vendido'` e `data_venda` preenchida. **Não apague o registro.** O ADCRM guarda o histórico dos leads por `veiculo_id`.
5. **Remover do estoque** (anúncio encerrado, carro devolvido, etc.): prefira marcar como `vendido`/inativo em vez de `DELETE`. Se apagar, apague também as fotos; o ADCRM trata carro inexistente como indisponível.
6. **Mudar de loja** (Recife para Olinda): só troca `status`. Os dois estoques são vendidos juntos; o status indica onde o carro está.

Efeito esperado no ADCRM: a página pública de um carro só existe enquanto ele está em `loja_recife` ou `loja_olinda` **e** tem foto. Carro vendido ou removido deixa a página indisponível automaticamente.

## 3. Imagens na nuvem (grátis)
- Armazenamento compatível com S3, bucket **privado**: recomendo **Cloudflare R2** (10 GB grátis, sem custo de saída de dados). Aceitável: Backblaze B2 (10 GB grátis), que o ADCRM já sabe ler. Qualquer um dos dois funciona com `boto3`, mudando apenas `endpoint`, chave e bucket.
- Configuração por `.env`: `B2_BUCKET`, `B2_KEY_ID`, `B2_APP_KEY`, `B2_ENDPOINT`, `B2_REGION`, `B2_PASTA_TAKES=takes`. A chave do objeto é `takes/<arquivo>`.
- No upload, o ROUTE deve:
  - validar o tipo (JPEG, PNG ou WebP) e limitar o tamanho (ex.: 15 MB);
  - redimensionar para no máximo 2000 px no lado maior e reencodar (WebP ou JPEG qualidade ~82), o que mantém o plano grátis por muito tempo;
  - remover metadados EXIF, principalmente GPS;
  - renomear para um nome único (uuid) para não haver colisão;
  - enviar ao bucket e só então gravar a linha em `estoque_takeveiculo` (se o envio falhar, nada é gravado);
  - gravar `largura`, `altura`, `ordem`, `categoria`, `destaque` e `foto = true`;
  - recalcular `possui_fotos` e `atualizado_em` do carro.
- Remover foto: `ativo = false`; a limpeza do objeto no bucket pode ser feita depois por uma rotina.
- Reordenar/trocar capa: atualiza `ordem` e `destaque` em lote, numa transação.
- O ROUTE não precisa gerar miniaturas: o ADCRM redimensiona sob demanda e guarda em cache.
- Migração: as fotos que já existem hoje devem ser aproveitadas. Se estiverem em outro lugar, escreva um comando de migração idempotente (que possa rodar de novo sem duplicar) e me dê o relatório do que foi migrado.

## 4. Vídeos: permanecem na máquina do ROUTE
- Observação: o ADCRM está num servidor e não enxerga o disco desta máquina. Por isso, por ora, o ADCRM só mostra a lista e a quantidade de vídeos de cada carro (nome, duração, tamanho). A reprodução remota fica como decisão futura (ex.: ROUTE publicar os vídeos por um túnel).
- Os vídeos **não vão para a nuvem**. Ficam numa pasta local configurável (`VIDEOS_DIR`), organizada por carro (`VIDEOS_DIR/<veiculo_id>/arquivo.mp4`).
- Para o ADCRM saber que existem, o ROUTE registra só os **metadados** na tabela `estoque_videoveiculo`:
  `id`, `veiculo_id`, `nome`, `caminho_local`, `tamanho_bytes`, `duracao_s` (null se desconhecida), `criado_em`, `ativo`.
- Aceite MP4/MOV/WebM, valide o tipo e o tamanho, e nunca aceite caminhos fora de `VIDEOS_DIR` (proteção contra `..`).
- Um carro pode ter vários vídeos, e cada vídeo pertence a um carro. Os vídeos de um carro aparecem juntos, numa janela própria dele.
- Quando o carro é vendido, os vídeos continuam no disco, mas ficam inativos.
- Os vídeos antigos que hoje estão em `estoque_takeveiculo` (arquivo terminado em .mp4/.mov/.webm) devem ser tratados no plano de migração da seção 0.

## 5. Segurança
- Nenhuma chave, senha ou token no código ou no Git. Tudo em `.env`, com `.env.example` sem valores reais.
- O bucket é privado; o ADCRM gera links assinados temporários.
- Registre em log quem alterou o estoque e quando (já existindo no ROUTE, mantenha).

## 6. Testes e entrega
- Testes automatizados para: cadastrar, editar, vender, remover, mudar de loja, subir/remover foto e o recálculo de `possui_fotos`.
- Um comando `estoque_verificar` que acusa: carro em loja sem foto, foto apontando para arquivo inexistente no bucket, `possui_fotos` inconsistente.
- Atualize o README do ROUTE com: variáveis do `.env`, como criar o banco e o bucket gratuitos, como rodar a migração e como restaurar o backup.
- Ao final, me entregue: (a) a URL somente leitura para o ADCRM, (b) os dados do bucket para leitura, (c) o resultado dos testes.
