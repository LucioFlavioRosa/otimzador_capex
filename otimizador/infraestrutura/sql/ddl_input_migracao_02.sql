-- Migração 02 — o recorte industrial vira recorte RESIDENCIAL, e sai da receita.
--
-- O QUE MUDA, E POR QUÊ
--
-- Antes: as colunas `*_industrial` guardavam a parcela industrial contida no total, e
-- `INCLUIR_INDUSTRIAL=False` subtraía essa parcela de LIGAÇÕES, RECEITA e VAZÃO, além de
-- estimar as economias residenciais por proporção. Ou seja: o recorte mexia no VPL, e a
-- rodada "só residencial" não era comparável com a outra.
--
-- Agora: o recorte acaba na COBERTURA. Quem paga a conta é a ligação, seja de casa ou de
-- fábrica — receita, VPL, vazão e CAPEX seguem no total em qualquer modo. O que é
-- residencial é a META, e ela passa a ser medida com colunas próprias, vindas do
-- Databricks já apuradas, e não deduzidas por subtração.
--
--   universo_ligacoes_residencial    quantas do universo são residenciais
--   ligacoes_atuais_residencial      quantas já atendidas são residenciais
--   universo_economias_residencial   idem, em economias
--   economias_atuais_residencial     idem
--
-- `receita_*_industrial` somem porque ticket médio industrial deixou de existir como
-- conceito no produto. `vazao_contribuicao_industrial` some porque a vazão dimensiona
-- módulo de ETE e rateia obra compartilhada — "demais", que segue no total: descontar
-- indústria ali subdimensionaria a estação.
--
-- POPULAÇÃO não ganha versão residencial: indústria não mora, então o universo de
-- população já é residencial por natureza. Cidade que mede cobertura em população ignora
-- as colunas novas.
--
-- MIGRAÇÃO DO DADO. As colunas novas nascem preenchidas por subtração a partir do que já
-- existe (`total − industrial`), e as economias pela densidade da própria sub-bacia. Isso
-- NÃO é a mesma coisa que o dado medido que vai passar a vir do Databricks: é o melhor
-- valor disponível hoje, para o banco não ficar com a coluna vazia até a próxima carga —
-- e sub-bacia sem parcela industrial fica igual ao total, que é a verdade dela.
--
-- Rode uma vez. É idempotente: `IF NOT EXISTS` nas adições, `IF EXISTS` nas remoções, e o
-- UPDATE só toca linha cuja coluna nova ainda está nula.
--
-- NULO NAO VIRA ZERO. `GREATEST` do Postgres IGNORA nulos — `GREATEST(0, NULL)` devolve
-- `0`, e nao `NULL`. Sem o `CASE` abaixo, uma sub-bacia com `universo_ligacoes` nulo
-- (desconhecido) receberia residencial `0` (nao tem universo), e o motor usaria esse
-- zero como denominador da meta, porque a coluna deixaria de ser nula. Base que ja
-- rodou a versao anterior desta migracao deve conferir:
--   SELECT count(*) FROM input.subbacia_operacional
--    WHERE universo_ligacoes IS NULL AND universo_ligacoes_residencial = 0;

BEGIN;

-- ---------------------------------------------------------------- sub-bacia
ALTER TABLE input.subbacia_operacional
  ADD COLUMN IF NOT EXISTS universo_ligacoes_residencial   integer,
  ADD COLUMN IF NOT EXISTS ligacoes_atuais_residencial     integer,
  ADD COLUMN IF NOT EXISTS universo_economias_residencial  integer,
  ADD COLUMN IF NOT EXISTS economias_atuais_residencial    integer;

UPDATE input.subbacia_operacional SET
  universo_ligacoes_residencial = CASE WHEN universo_ligacoes IS NULL THEN NULL
    ELSE GREATEST(0, universo_ligacoes - COALESCE(universo_ligacoes_industrial, 0)) END,
  ligacoes_atuais_residencial   = CASE WHEN ligacoes_atuais IS NULL THEN NULL
    ELSE GREATEST(0, ligacoes_atuais - COALESCE(ligacoes_atuais_industrial, 0)) END,
  -- Economias residenciais pela densidade da própria sub-bacia (economias por ligação).
  -- Sem universo de ligações não há densidade: fica nulo, e a engine cai para o total
  -- nessa sub-bacia, avisando.
  universo_economias_residencial = CASE WHEN COALESCE(universo_ligacoes, 0) > 0
    THEN ROUND(universo_economias::numeric
               * GREATEST(0, universo_ligacoes - COALESCE(universo_ligacoes_industrial, 0))::numeric
               / universo_ligacoes::numeric)   -- aqui o CASE ja garante universo NAO nulo
    END,
  economias_atuais_residencial = CASE WHEN COALESCE(ligacoes_atuais, 0) > 0
    THEN ROUND(economias_atuais::numeric
               * GREATEST(0, ligacoes_atuais - COALESCE(ligacoes_atuais_industrial, 0))::numeric
               / ligacoes_atuais::numeric)     -- idem
    END
WHERE universo_ligacoes_residencial IS NULL;

ALTER TABLE input.subbacia_operacional
  DROP COLUMN IF EXISTS universo_ligacoes_industrial,
  DROP COLUMN IF EXISTS ligacoes_atuais_industrial,
  DROP COLUMN IF EXISTS receita_faturada_industrial,
  DROP COLUMN IF EXISTS receita_arrecadada_industrial,
  DROP COLUMN IF EXISTS vazao_contribuicao_industrial;

-- ---------------------------------------------------------------- CTS
-- A CTS é a irmã da sub-bacia e tem o mesmo campo-set: o que entra numa entra na outra,
-- senão a cobertura residencial ignoraria a demanda que a CTS traz.
ALTER TABLE input.cts_operacional
  ADD COLUMN IF NOT EXISTS universo_ligacoes_residencial   integer,
  ADD COLUMN IF NOT EXISTS ligacoes_atuais_residencial     integer,
  ADD COLUMN IF NOT EXISTS universo_economias_residencial  integer,
  ADD COLUMN IF NOT EXISTS economias_atuais_residencial    integer;

UPDATE input.cts_operacional SET
  universo_ligacoes_residencial = CASE WHEN universo_ligacoes IS NULL THEN NULL
    ELSE GREATEST(0, universo_ligacoes - COALESCE(universo_ligacoes_industrial, 0)) END,
  ligacoes_atuais_residencial   = CASE WHEN ligacoes_atuais IS NULL THEN NULL
    ELSE GREATEST(0, ligacoes_atuais - COALESCE(ligacoes_atuais_industrial, 0)) END,
  universo_economias_residencial = CASE WHEN COALESCE(universo_ligacoes, 0) > 0
    THEN ROUND(universo_economias::numeric
               * GREATEST(0, universo_ligacoes - COALESCE(universo_ligacoes_industrial, 0))::numeric
               / universo_ligacoes::numeric)   -- aqui o CASE ja garante universo NAO nulo
    END,
  economias_atuais_residencial = CASE WHEN COALESCE(ligacoes_atuais, 0) > 0
    THEN ROUND(economias_atuais::numeric
               * GREATEST(0, ligacoes_atuais - COALESCE(ligacoes_atuais_industrial, 0))::numeric
               / ligacoes_atuais::numeric)     -- idem
    END
WHERE universo_ligacoes_residencial IS NULL;

ALTER TABLE input.cts_operacional
  DROP COLUMN IF EXISTS universo_ligacoes_industrial,
  DROP COLUMN IF EXISTS ligacoes_atuais_industrial,
  DROP COLUMN IF EXISTS receita_faturada_industrial,
  DROP COLUMN IF EXISTS receita_arrecadada_industrial,
  DROP COLUMN IF EXISTS vazao_contribuicao_industrial;

COMMIT;
