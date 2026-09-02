-- SUPERADA PELA MIGRACAO 016 DO SERVICO (`otimizador-backend/migracoes/`).
--
-- A decisao de usar sistema de CTS passou a ser da UNIDADE, e a coluna que esta
-- migracao criou em `cidade_sistema` foi REMOVIDA. O arquivo fica como historico
-- — e o que explica as 142 linhas de trilha com `tipo = 'sistema'`. NAO aplique
-- num banco novo: `ddl_input.sql` ja traz a coluna no lugar certo
-- (`unidade_regional`), e rodar isto depois recria uma coluna morta.
--
-- Migração 06 — o sistema declara se usa SISTEMA DE CTS.
--
-- O QUE MUDA. `cidade_sistema` ganha `usa_sistema_cts boolean`. Marcado, o
-- sistema aceita UMA CTS. Desmarcado, aceita quantas forem cadastradas nele.
--
-- NASCE DESMARCADO, e isso não é escolha estética: no cadastro atual 32 sistemas
-- já têm 2 CTS cada (123 têm 1). Um default marcado tornaria esses 32
-- inválidos no instante da migração, e o servidor passaria a recusar gravações
-- de topologia que nunca foram erradas. Quem marcar depois é quem cadastra, e aí
-- a recusa tem dono e motivo.
--
-- QUEM PREENCHE É A REGIONAL, e não o Databricks — como
-- `componente_sistema_id_jusante`, que também mora numa tabela carregada de fora.
-- A consequência é a mesma dos dois: se a carga do Databricks REESCREVER a
-- tabela em vez de atualizar as colunas dela, o valor se perde. Isso vale hoje
-- para o caminho até a ETE, e passa a valer para este campo — o carregador
-- precisa preservar as colunas de cadastro, ou elas voltam ao default a cada
-- carga, em silêncio.
--
-- POR QUE NA TABELA DO SISTEMA. É atributo do sistema, não da CTS: a pergunta
-- "quantas CTS este sistema comporta" só tem uma resposta por sistema, e guardá-la
-- na CTS a repetiria em cada uma, com o risco de duas discordarem.
--
-- O MOTOR IGNORA ESTA COLUNA. Ela é regra de CADASTRO — quantas CTS podem ser
-- colocadas —, e não de otimização. Para o motor, uma ou duas CTS num sistema são
-- nós como quaisquer outros; ele nunca contou quantas havia.
--
-- Idempotente: `ADD COLUMN IF NOT EXISTS`.

BEGIN;

ALTER TABLE input.cidade_sistema
  ADD COLUMN IF NOT EXISTS usa_sistema_cts boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN input.cidade_sistema.usa_sistema_cts IS
  'Marcado: o sistema aceita UMA CTS. Desmarcado: aceita várias. Preenchido pela Regional no cadastro (Grupo 01), não vem do Databricks. O motor ignora — é regra de cadastro, não de otimização.';

COMMIT;
