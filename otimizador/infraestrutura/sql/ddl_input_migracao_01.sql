-- ATENCAO: `cidade_operacional.unidade_cobertura` FOI REMOVIDA (migracao 019 do
-- servico). A regua da cobertura virou parametro de rodada (`UNIDADE_COBERTURA`).
-- Este arquivo fica como historico; aplica-lo num banco novo recria uma coluna
-- morta, que o motor nao le mais.
--
-- ============================================================================
-- MIGRATION 01 — leva um banco criado com o ddl_input.sql ANTIGO ate o novo.
-- Banco NOVO nao precisa desta migration: rode `ddl_input.sql` direto.
--
-- Roda em UMA transacao: ou aplica tudo, ou nada. Se algum ALTER falhar, o motivo
-- e quase sempre dado sujo (duplicata na futura PK, orfao na futura FK) — as
-- consultas de diagnostico no fim deste arquivo mostram onde.
-- ============================================================================
BEGIN;

-- ---- 1. colunas de comentario que vazaram do Excel de amostra --------------
ALTER TABLE input.cidade_operacional DROP COLUMN IF EXISTS "Unnamed: 3";
ALTER TABLE input.cidade_operacional DROP COLUMN IF EXISTS
    "unidade_cobertura: ligacoes | economias | populacao. Define a REGUA da meta e da faixa de paridade daquela cidade. A receita continua sempre em ligacoes.";
ALTER TABLE input.fator_esgoto DROP COLUMN IF EXISTS "Unnamed: 4";
ALTER TABLE input.fator_esgoto DROP COLUMN IF EXISTS
    "PARIDADE esgoto/agua: tarifa_esgoto = ticket_medio(agua) x paridade. Regra: vale a paridade da MAIOR faixa cuja cobertura_pct <= cobertura da cidade no ano (cobertura REALIZADA do plano). Cidade com paridade constante: uma unica linha com cobertura_pct=0.";

COMMENT ON COLUMN input.cidade_operacional.unidade_cobertura IS
    'ligacoes | economias | populacao. Define a REGUA da meta e da faixa de paridade '
    'daquela cidade. A receita continua sempre em ligacoes.';

-- ---- 2. tipos: `integer` arredonda em silencio, `text` nao valida ----------
ALTER TABLE input.subbacia_operacional
    ALTER COLUMN universo_populacao    TYPE double precision USING NULLIF(universo_populacao,'')::double precision,
    ALTER COLUMN populacao_atual       TYPE double precision USING NULLIF(populacao_atual,'')::double precision,
    ALTER COLUMN populacao_novas_obras TYPE double precision USING NULLIF(populacao_novas_obras,'')::double precision,
    ALTER COLUMN potencial_crescimento TYPE double precision;   -- era integer: 1,5 virava 2

ALTER TABLE input.cts_operacional
    ALTER COLUMN universo_populacao    TYPE double precision USING NULLIF(universo_populacao,'')::double precision,
    ALTER COLUMN populacao_atual       TYPE double precision USING NULLIF(populacao_atual,'')::double precision,
    ALTER COLUMN populacao_novas_obras TYPE double precision USING NULLIF(populacao_novas_obras,'')::double precision;

ALTER TABLE input.metas_cobertura ALTER COLUMN cobertura_pct TYPE double precision;
ALTER TABLE input.fator_esgoto    ALTER COLUMN cobertura_pct TYPE double precision;

-- mesma coluna, tipos diferentes nas duas tabelas irmas
ALTER TABLE input.componentes_cts_capex ALTER COLUMN quantidade TYPE double precision;

-- ---- 3. chaves primarias --------------------------------------------------
-- duplicata em componentes-*-capex DUPLICA a obra (CAPEX conta duas vezes) e passa
-- em todas as reconciliacoes do portao. Em subbacia-operacional a ultima linha vence.
ALTER TABLE input.unidade_regional            ADD PRIMARY KEY (unidade_id);
ALTER TABLE input.regional_superintendencia   ADD PRIMARY KEY (superintendencia_id);
ALTER TABLE input.superintendencia_cidade     ADD PRIMARY KEY (cidade_id);
ALTER TABLE input.cidade_sistema              ADD PRIMARY KEY (sistema_id);
-- PK no id do componente SOZINHO: o motor indexa os nos por id GLOBAL
-- (`self.nos = {n.id: n for n in nos}`, otimizador_capex_v62.py:63). Id repetido em outro
-- sistema seria aceito pelo banco e o motor manteria so o ultimo, perdendo um no.
ALTER TABLE input.sistema_topologia           ADD PRIMARY KEY (componente_sistema_id);
ALTER TABLE input.cidade_operacional          ADD PRIMARY KEY (cidade_id);
ALTER TABLE input.subbacia_operacional        ADD PRIMARY KEY (sub_bacia);
ALTER TABLE input.componentes_subbacias_capex ADD PRIMARY KEY (sub_bacia, componente);
ALTER TABLE input.ete_capex                   ADD PRIMARY KEY (ete_id);
ALTER TABLE input.regional_operacional        ADD PRIMARY KEY (regional_id);
ALTER TABLE input.metas_cobertura             ADD PRIMARY KEY (cidade_id, ano);
ALTER TABLE input.fator_esgoto                ADD PRIMARY KEY (cidade_id, cobertura_pct);
ALTER TABLE input.subbacia_cts                ADD PRIMARY KEY (sub_bacia);
ALTER TABLE input.cts_operacional             ADD PRIMARY KEY (cts);
ALTER TABLE input.componentes_cts_capex       ADD PRIMARY KEY (cts, componente);

-- ---- 4. FKs da hierarquia (elo quebrado = sub-bacia orfa, some sem aviso) --
ALTER TABLE input.regional_superintendencia   ADD CONSTRAINT fk_sup_unidade
    FOREIGN KEY (unidade_id)          REFERENCES input.unidade_regional(unidade_id);
ALTER TABLE input.superintendencia_cidade     ADD CONSTRAINT fk_cid_sup
    FOREIGN KEY (superintendencia_id) REFERENCES input.regional_superintendencia(superintendencia_id);
ALTER TABLE input.cidade_sistema              ADD CONSTRAINT fk_sis_cidade
    FOREIGN KEY (cidade_id)           REFERENCES input.superintendencia_cidade(cidade_id);
ALTER TABLE input.sistema_topologia           ADD CONSTRAINT fk_topo_sistema
    FOREIGN KEY (sistema_id)          REFERENCES input.cidade_sistema(sistema_id);
ALTER TABLE input.cidade_operacional          ADD CONSTRAINT fk_cidop_cidade
    FOREIGN KEY (cidade_id)           REFERENCES input.superintendencia_cidade(cidade_id);
ALTER TABLE input.componentes_subbacias_capex ADD CONSTRAINT fk_comp_sub
    FOREIGN KEY (sub_bacia)           REFERENCES input.subbacia_operacional(sub_bacia);
ALTER TABLE input.componentes_cts_capex       ADD CONSTRAINT fk_comp_cts
    FOREIGN KEY (cts)                 REFERENCES input.cts_operacional(cts);
ALTER TABLE input.subbacia_cts                ADD CONSTRAINT fk_subcts_sub
    FOREIGN KEY (sub_bacia)           REFERENCES input.subbacia_operacional(sub_bacia);
ALTER TABLE input.subbacia_cts                ADD CONSTRAINT fk_subcts_cts
    FOREIGN KEY (cts)                 REFERENCES input.cts_operacional(cts);
-- metas_cobertura / fator_esgoto FICAM SEM FK de proposito: sao carregadas antes do
-- cadastro completo da cidade.

-- ---- 5. indices -----------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_unidade_regional ON input.unidade_regional (regional_id);
CREATE INDEX IF NOT EXISTS ix_sup_unidade      ON input.regional_superintendencia (unidade_id);
CREATE INDEX IF NOT EXISTS ix_cidade_sup       ON input.superintendencia_cidade (superintendencia_id);
CREATE INDEX IF NOT EXISTS ix_sistema_cidade   ON input.cidade_sistema (cidade_id);
CREATE INDEX IF NOT EXISTS ix_topo_sistema     ON input.sistema_topologia (sistema_id);
CREATE INDEX IF NOT EXISTS ix_topo_jusante     ON input.sistema_topologia (componente_sistema_id_jusante);
CREATE INDEX IF NOT EXISTS ix_metas_cidade     ON input.metas_cobertura (cidade_id);
CREATE INDEX IF NOT EXISTS ix_fator_cidade     ON input.fator_esgoto (cidade_id);

-- ---- 6. a aba `orcamento` que o motor le e nao existia no schema -----------
CREATE TABLE IF NOT EXISTS input.orcamento (
    regional_id text PRIMARY KEY,
    valor_ano   double precision NOT NULL
);

-- ---- 7. controle: coerencia de estados e rastro ---------------------------
-- defensivo: a FK abaixo precisa do alvo. Bancos criados pelo ddl_input.sql antigo ja tem
-- run_request; um banco montado a mao pode nao ter.
CREATE TABLE IF NOT EXISTS controle.run_request (
    run_id        text PRIMARY KEY,
    unidade       text,
    params        jsonb NOT NULL,
    solicitado_por text,
    solicitado_em timestamptz DEFAULT now()
);

ALTER TABLE controle.run_status ADD CONSTRAINT ck_status
    CHECK (status IN ('PENDENTE','RODANDO','SUCESSO','FALHOU_QUALIDADE','ERRO'));
ALTER TABLE controle.run_status ADD CONSTRAINT fk_status_request
    FOREIGN KEY (run_id) REFERENCES controle.run_request(run_id);

COMMIT;

-- ============================================================================
-- DIAGNOSTICO — rode ANTES da migration se algum ALTER falhar.
-- ============================================================================
-- duplicatas que impedem a PK:
--   SELECT sub_bacia, componente, count(*) FROM input.componentes_subbacias_capex
--    GROUP BY 1,2 HAVING count(*) > 1;
--   SELECT sub_bacia, count(*) FROM input.subbacia_operacional
--    GROUP BY 1 HAVING count(*) > 1;
--   -- id de no repetido entre sistemas (o motor so ficaria com o ultimo):
--   SELECT componente_sistema_id, count(*) FROM input.sistema_topologia
--    GROUP BY 1 HAVING count(*) > 1;
--
-- orfaos que impedem a FK:
--   SELECT c.* FROM input.componentes_subbacias_capex c
--    LEFT JOIN input.subbacia_operacional s USING (sub_bacia) WHERE s.sub_bacia IS NULL;
--   SELECT sc.* FROM input.superintendencia_cidade sc
--    LEFT JOIN input.regional_superintendencia r USING (superintendencia_id)
--    WHERE r.superintendencia_id IS NULL;
--
-- texto que nao converte para numero:
--   SELECT sub_bacia, universo_populacao FROM input.subbacia_operacional
--    WHERE universo_populacao !~ '^\s*-?\d+([.,]\d+)?\s*$' AND universo_populacao <> '';
