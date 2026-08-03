-- ============================================================================
-- DDL — RESULTADO (public.otim_*)  |  Postgres/Azure
--
-- GERADO por `python main.py gerar-ddl`. Reflete exatamente o que
-- `publicacao.publicar_postgres` escreve. NAO edite a mao: um esquema divergente do
-- gerado faz o INSERT falhar com erro obscuro, ou aceita numero em coluna TEXT e quebra
-- ORDER BY/SUM no front.
--
-- Aplique como MIGRATION, uma vez. O job publica com `criar_schema=False` — DDL nao roda
-- no caminho quente (tomaria lock, e `CREATE TABLE IF NOT EXISTS` nao adiciona coluna
-- nova numa tabela que ja existe).
--
-- 14 tabelas + 3 views. Toda tabela de detalhe tem FK para otim_meta com ON DELETE
-- CASCADE: e o que faz a republicacao de um run_id ficar limpa (apaga e regrava).
--
-- Dicionario de colunas: docs/06-dicionario-resultado.md
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS public;

CREATE TABLE IF NOT EXISTS public.otim_meta (
    run_id TEXT,
    data_hora TIMESTAMPTZ,
    engine TEXT,
    engine_arquivo TEXT,
    engine_md5 TEXT,
    banco_arquivo TEXT,
    banco_md5 TEXT,
    regional TEXT,
    anos_horizonte BIGINT,
    anos_capex BIGINT,
    ano_base BIGINT,
    ete_faseada BOOLEAN,
    curva_adocao TEXT,
    foco_cobertura DOUBLE PRECISION,
    penalidade_cobertura TEXT,
    peso_cobertura DOUBLE PRECISION,
    peso_cidade JSONB,
    orcamento_por_ano JSONB,
    orcamento_total DOUBLE PRECISION,
    params_extra JSONB,
    milp_status TEXT,
    milp_solver TEXT,
    milp_bound DOUBLE PRECISION,
    vpl DOUBLE PRECISION,
    vpl_obj DOUBLE PRECISION,
    vp_efeito_base DOUBLE PRECISION,
    capex_total DOUBLE PRECISION,
    opex_total DOUBLE PRECISION,
    receita_total DOUBLE PRECISION,
    obras_total BIGINT,
    obras_construidas BIGINT,
    obrig_total BIGINT,
    obrig_construidas BIGINT,
    obrig_desconsideradas JSONB,
    subbacias_total BIGINT,
    subbacias_faturando BIGINT,
    metas_total BIGINT,
    metas_nao_atingidas BIGINT,
    deficit_cobertura DOUBLE PRECISION,
    auditoria_ok BOOLEAN,
    auditoria_reparos BIGINT,
    aviso_orcamento TEXT,
    aviso_obrigatoria TEXT,
    status_execucao TEXT,
    rotulo TEXT,
    usuario TEXT,
    erro TEXT,
    blob_uri TEXT,
    cobertura_final_pct DOUBLE PRECISION,
    tempo_s DOUBLE PRECISION,
    PRIMARY KEY (run_id)
);

CREATE TABLE IF NOT EXISTS public.otim_obra (
    run_id TEXT,
    obra_id TEXT,
    tipo TEXT,
    componente TEXT,
    no TEXT,
    sistema TEXT,
    is_cts BOOLEAN,
    cidade TEXT,
    regional TEXT,
    responsavel TEXT,
    necessaria BOOLEAN,
    capex DOUBLE PRECISION,
    capex_componentes JSONB,
    quantidade DOUBLE PRECISION,
    unidade TEXT,
    preco_unitario DOUBLE PRECISION,
    opex_ano DOUBLE PRECISION,
    prazo_meses BIGINT,
    prazo_inicio_meses BIGINT,
    inicio_min_mes BIGINT,
    obrigatoria BOOLEAN,
    obrig_ano_plano BIGINT,
    proibida_ate BIGINT,
    proibida_nunca BOOLEAN,
    ligacoes DOUBLE PRECISION,
    ticket_mes DOUBLE PRECISION,
    preco_ligacao DOUBLE PRECISION,
    arrec_dir DOUBLE PRECISION,
    arrec_ind DOUBLE PRECISION,
    lag_meses BIGINT,
    maturacao_meses BIGINT,
    wacc DOUBLE PRECISION,
    wacc_origem TEXT,
    mes_inicio BIGINT,
    data_inicio TEXT,
    mes_pronta BIGINT,
    data_pronta TEXT,
    construida BOOLEAN,
    faturando BOOLEAN,
    mes_inicio_faturamento BIGINT,
    data_inicio_faturamento TEXT,
    status TEXT,
    categoria_motivo TEXT,
    motivo TEXT,
    elo_que_trava TEXT,
    saldo_potencial DOUBLE PRECISION,
    PRIMARY KEY (run_id, obra_id),
    CONSTRAINT fk_obra_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_obra_run_id_cidade ON public.otim_obra (run_id, cidade);
CREATE INDEX IF NOT EXISTS ix_obra_run_id_status ON public.otim_obra (run_id, status);
CREATE INDEX IF NOT EXISTS ix_obra_run_id_categoria_motivo ON public.otim_obra (run_id, categoria_motivo);

CREATE TABLE IF NOT EXISTS public.otim_subbacia (
    run_id TEXT,
    sub_bacia TEXT,
    cidade TEXT,
    sistema TEXT,
    regional TEXT,
    jusante TEXT,
    is_cts BOOLEAN,
    tipo_estrutura TEXT,
    vazao_marginal DOUBLE PRECISION,
    unid_fator_cobertura DOUBLE PRECISION,
    ligacoes_atuais DOUBLE PRECISION,
    ticket_medio DOUBLE PRECISION,
    arrecadacao DOUBLE PRECISION,
    ligacoes_novas DOUBLE PRECISION,
    obra_coleta TEXT,
    faturando BOOLEAN,
    mes_inicio_faturamento BIGINT,
    data_inicio_faturamento TEXT,
    motivo_sem_receita TEXT,
    vpl DOUBLE PRECISION,
    vp_capex_rateado DOUBLE PRECISION,
    vp_opex_rateado DOUBLE PRECISION,
    vp_receita_direta DOUBLE PRECISION,
    vp_receita_indireta DOUBLE PRECISION,
    vp_efeito_base DOUBLE PRECISION,
    pot_vp_receita DOUBLE PRECISION,
    pot_vp_capex_solo DOUBLE PRECISION,
    pot_vp_capex_rateado DOUBLE PRECISION,
    pot_vp_opex DOUBLE PRECISION,
    pot_saldo_solo DOUBLE PRECISION,
    pot_saldo_rateado DOUBLE PRECISION,
    pot_obras_faltantes BIGINT,
    densidade_economias DOUBLE PRECISION,
    densidade_populacao DOUBLE PRECISION,
    unidade_cobertura TEXT,
    fator_unidade_cobertura DOUBLE PRECISION,
    potencial_crescimento DOUBLE PRECISION,
    wacc_receita DOUBLE PRECISION,
    horizonte_anos BIGINT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    PRIMARY KEY (run_id, sub_bacia),
    CONSTRAINT fk_subbacia_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_subbacia_run_id_cidade ON public.otim_subbacia (run_id, cidade);
CREATE INDEX IF NOT EXISTS ix_subbacia_run_id_faturando ON public.otim_subbacia (run_id, faturando);

CREATE TABLE IF NOT EXISTS public.otim_subbacia_ano (
    run_id TEXT,
    sub_bacia TEXT,
    cidade TEXT,
    sistema TEXT,
    ano BIGINT,
    receita_direta DOUBLE PRECISION,
    receita_indireta DOUBLE PRECISION,
    efeito_base DOUBLE PRECISION,
    capex_rateado DOUBLE PRECISION,
    opex_rateado DOUBLE PRECISION,
    ebitda DOUBLE PRECISION,
    faturando BOOLEAN,
    PRIMARY KEY (run_id, sub_bacia, ano),
    CONSTRAINT fk_subbacia_ano_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_subbacia_ano_run_id_sub_bacia ON public.otim_subbacia_ano (run_id, sub_bacia);
CREATE INDEX IF NOT EXISTS ix_subbacia_ano_run_id_cidade_ano ON public.otim_subbacia_ano (run_id, cidade, ano);

CREATE TABLE IF NOT EXISTS public.otim_sistema (
    run_id TEXT,
    sistema TEXT,
    cidade TEXT,
    horizonte_anos BIGINT,
    ano_fim_concessao BIGINT,
    sub_bacias BIGINT,
    sub_bacias_faturando BIGINT,
    ete_id TEXT,
    ete_nova BOOLEAN,
    ete_responsavel TEXT,
    folga_inicial DOUBLE PRECISION,
    capacidade_modulo DOUBLE PRECISION,
    capex_modulo DOUBLE PRECISION,
    capex_terreno DOUBLE PRECISION,
    modulos_disponiveis BIGINT,
    modulos_construidos BIGINT,
    capex_modulos_construidos DOUBLE PRECISION,
    capacidade_instalada DOUBLE PRECISION,
    vazao_conectada DOUBLE PRECISION,
    vazao_total_sistema DOUBLE PRECISION,
    ocupacao_pct DOUBLE PRECISION,
    folga_remanescente DOUBLE PRECISION,
    vazao_nao_atendida DOUBLE PRECISION,
    primeiro_modulo_mes BIGINT,
    PRIMARY KEY (run_id, sistema),
    CONSTRAINT fk_sistema_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_sistema_run_id_cidade ON public.otim_sistema (run_id, cidade);

CREATE TABLE IF NOT EXISTS public.otim_dependencia (
    run_id TEXT,
    obra_id TEXT,
    obra_tipo TEXT,
    sub_bacia TEXT,
    cidade TEXT,
    sistema TEXT,
    vazao_sub_bacia DOUBLE PRECISION,
    vazao_total_obra DOUBLE PRECISION,
    fracao_rateio DOUBLE PRECISION,
    capex_rateado DOUBLE PRECISION,
    n_dependentes BIGINT,
    obra_construida BOOLEAN,
    sub_bacia_faturando BOOLEAN,
    PRIMARY KEY (run_id, obra_id, sub_bacia),
    CONSTRAINT fk_dependencia_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_dependencia_run_id_sub_bacia ON public.otim_dependencia (run_id, sub_bacia);
CREATE INDEX IF NOT EXISTS ix_dependencia_run_id_obra_id ON public.otim_dependencia (run_id, obra_id);

CREATE TABLE IF NOT EXISTS public.otim_ano (
    run_id TEXT,
    ano BIGINT,
    ano_indice BIGINT,
    capex DOUBLE PRECISION,
    opex DOUBLE PRECISION,
    receita DOUBLE PRECISION,
    receita_efeito_base DOUBLE PRECISION,
    receita_total DOUBLE PRECISION,
    ebitda DOUBLE PRECISION,
    ebitda_acumulado DOUBLE PRECISION,
    ebitda_margem_pct DOUBLE PRECISION,
    teto_capex DOUBLE PRECISION,
    uso_teto_pct DOUBLE PRECISION,
    excesso DOUBLE PRECISION,
    dentro_janela_capex BOOLEAN,
    PRIMARY KEY (run_id, ano),
    CONSTRAINT fk_ano_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.otim_mes (
    run_id TEXT,
    mes_indice BIGINT,
    ano BIGINT,
    mes BIGINT,
    competencia TEXT,
    capex_mes DOUBLE PRECISION,
    capex_acumulado DOUBLE PRECISION,
    PRIMARY KEY (run_id, mes_indice),
    CONSTRAINT fk_mes_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_mes_run_id_ano ON public.otim_mes (run_id, ano);

CREATE TABLE IF NOT EXISTS public.otim_cidade (
    run_id TEXT,
    cidade TEXT,
    sub_bacias BIGINT,
    obras_feitas BIGINT,
    obras_fora BIGINT,
    capex_total DOUBLE PRECISION,
    vpl DOUBLE PRECISION,
    ligacoes_novas DOUBLE PRECISION,
    universo DOUBLE PRECISION,
    base_atendida DOUBLE PRECISION,
    cobertura_base_pct DOUBLE PRECISION,
    cobertura_final_pct DOUBLE PRECISION,
    metas_total BIGINT,
    metas_atingidas BIGINT,
    paridade_inicial DOUBLE PRECISION,
    paridade_final DOUBLE PRECISION,
    peso_cidade JSONB,
    unidade_cobertura TEXT,
    PRIMARY KEY (run_id, cidade),
    CONSTRAINT fk_cidade_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.otim_cidade_ano (
    run_id TEXT,
    cidade TEXT,
    ano BIGINT,
    capex DOUBLE PRECISION,
    PRIMARY KEY (run_id, cidade, ano),
    CONSTRAINT fk_cidade_ano_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.otim_cobertura (
    run_id TEXT,
    cidade TEXT,
    ano BIGINT,
    ligacoes_cobertas DOUBLE PRECISION,
    universo DOUBLE PRECISION,
    cobertura_pct DOUBLE PRECISION,
    PRIMARY KEY (run_id, cidade, ano),
    CONSTRAINT fk_cobertura_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_cobertura_run_id_cidade ON public.otim_cobertura (run_id, cidade);

CREATE TABLE IF NOT EXISTS public.otim_meta_cobertura (
    run_id TEXT,
    cidade TEXT,
    ano BIGINT,
    pct_alvo DOUBLE PRECISION,
    alvo_ligacoes DOUBLE PRECISION,
    cobertura_ligacoes DOUBLE PRECISION,
    deficit_ligacoes DOUBLE PRECISION,
    atingida BOOLEAN,
    dentro_janela_capex BOOLEAN,
    PRIMARY KEY (run_id, cidade, ano),
    CONSTRAINT fk_meta_cobertura_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.otim_paridade (
    run_id TEXT,
    cidade TEXT,
    ano BIGINT,
    paridade DOUBLE PRECISION,
    paridade_base DOUBLE PRECISION,
    delta_paridade DOUBLE PRECISION,
    PRIMARY KEY (run_id, cidade, ano),
    CONSTRAINT fk_paridade_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.otim_auditoria (
    run_id TEXT,
    tipo TEXT,
    ano BIGINT,
    gasto DOUBLE PRECISION,
    teto DOUBLE PRECISION,
    excesso DOUBLE PRECISION,
    detalhe JSONB,
    CONSTRAINT fk_auditoria_run FOREIGN KEY (run_id) REFERENCES public.otim_meta(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_meta_data ON public.otim_meta (data_hora DESC);
CREATE INDEX IF NOT EXISTS ix_meta_status ON public.otim_meta (status_execucao);

-- ---------------------------------------------------------------- VIEWS
-- 1) Lista de historico: e o que a tela inicial consome, sem nenhum join.
CREATE OR REPLACE VIEW public.otim_vw_historico AS
SELECT run_id, rotulo, usuario, data_hora, status_execucao, milp_status,
       anos_capex, orcamento_total, vpl, capex_total, obras_construidas, obras_total,
       subbacias_faturando, subbacias_total, cobertura_final_pct,
       metas_total, metas_nao_atingidas, vp_efeito_base, auditoria_ok, tempo_s
FROM public.otim_meta
ORDER BY data_hora DESC;

-- 2) Obras que ficaram de fora, com o diagnostico ja montado.
CREATE OR REPLACE VIEW public.otim_vw_obra_fora AS
SELECT run_id, obra_id, tipo, cidade, no AS sub_bacia, capex, ligacoes,
       categoria_motivo, elo_que_trava, saldo_potencial, motivo
FROM public.otim_obra
WHERE status = 'FORA';

-- 3) Topologia: arestas ja enriquecidas com os atributos da obra.
CREATE OR REPLACE VIEW public.otim_vw_topologia AS
SELECT d.run_id, d.sub_bacia, d.obra_id, d.obra_tipo, d.cidade, d.sistema,
       d.fracao_rateio, d.capex_rateado, d.n_dependentes,
       d.obra_construida, d.sub_bacia_faturando,
       o.capex, o.data_inicio, o.data_pronta, o.responsavel, o.status
FROM public.otim_dependencia d
JOIN public.otim_obra o ON o.run_id = d.run_id AND o.obra_id = d.obra_id;
