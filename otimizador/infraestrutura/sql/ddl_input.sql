-- ============================================================================
-- DDL — INPUT (cadastro) + CONTROLE  (Postgres/Azure)
-- As tabelas run_* de RESULTADO tem DDL propria em publicacao.ddl_postgres(tabs).
--
-- Este script e para BANCO NOVO (roda inteiro, na ordem). Para um banco que ja
-- existe sem chaves/tipos, use `ddl_input_migracao_01.sql`.
--
-- Decisoes desta versao (ver REVISAO_PRODUCAO.md, achados A5/M1/M2/C4):
--   • PK em toda tabela. Duplicata no cadastro corrompe o plano EM SILENCIO: em
--     `subbacia-operacional` a ultima linha vence (some uma sub-bacia) e em
--     `componentes-*-capex` a obra e DUPLICADA (o CAPEX conta duas vezes). Nenhum
--     dos dois aparece como erro — as reconciliacoes do portao fecham normalmente.
--   • FK nas tabelas de HIERARQUIA, que o motor navega. Elo quebrado = sub-bacia
--     orfa, que some do resultado sem aviso.
--   • Tipos numericos onde o gerador inferiu `text`/`integer` por amostra: em
--     coluna `integer` o Postgres ARREDONDA em silencio (meta de 90,5% vira 90;
--     crescimento de 1,5 vira 2).
--   • As colunas de comentario que vazaram do Excel de amostra ("Unnamed: 3" e a
--     coluna cujo nome era uma frase inteira) viraram COMMENT ON COLUMN.
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS input;
CREATE SCHEMA IF NOT EXISTS controle;

-- ---- HIERARQUIA -----------------------------------------------------------
-- aba do motor: unidade-regional
-- `usa_sistema_cts` e da REGIONAL, e nao do Databricks: marcado, CADA sistema da
-- unidade aceita UMA CTS; desmarcado, aceitam varias. Regra de cadastro — o
-- motor ignora, porque para ele uma ou duas CTS sao nos como quaisquer outros.
--
-- ESTAVA EM `cidade_sistema`, uma linha por sistema, ate a migracao 016 do
-- servico. A decisao e da unidade: quem opera decide uma vez e vale para todos os
-- sistemas dentro dela.
CREATE TABLE IF NOT EXISTS input.unidade_regional (
    unidade_id      text PRIMARY KEY,
    unidade_name    text,
    regional_id     text NOT NULL,
    regional_name   text,
    wacc_medio      double precision,
    usa_sistema_cts boolean NOT NULL DEFAULT false
);

-- HIERARQUIA v8: a EMPRESA OPERADORA no lugar da superintendencia.
--
-- A superintendencia era um nivel de reserva que fonte nenhuma trazia. A v8 a substituiu
-- pela empresa, que e real e vem do de-para, e partiu o antigo `superintendencia_cidade`
-- em duas: o municipio passa a existir por si (`cidade`) e o vinculo fica em
-- `cidade_empresa`.
--
-- O MOTOR NAO MUDOU DE VOCABULARIO: ele ainda le as abas `regional-superintendencia` e
-- `superintendencia-cidade`. Quem traduz e `carregar_postgres.ABAS_INPUT`, com um `AS`
-- que projeta `emp_codigo` como `superintendencia_id`. As tabelas aqui sao as do
-- CADASTRO; as abas sao o que o motor pede.
CREATE TABLE IF NOT EXISTS input.empresa (
    emp_codigo         text PRIMARY KEY,
    empresa            text,
    unidade_id         text NOT NULL
        REFERENCES input.unidade_regional(unidade_id),
    data_fim_concessao integer
);

CREATE TABLE IF NOT EXISTS input.cidade (
    cidade_id   text PRIMARY KEY,
    cidade_name text
);

CREATE TABLE IF NOT EXISTS input.cidade_empresa (
    cidade_id  text PRIMARY KEY REFERENCES input.cidade(cidade_id),
    emp_codigo text NOT NULL REFERENCES input.empresa(emp_codigo)
);

-- aba do motor: cidade-sistema
--
-- `usa_sistema_cts` SAIU DAQUI: a decisao passou a ser da unidade (ver
-- `unidade_regional` acima), e vale para todos os sistemas dentro dela.
CREATE TABLE IF NOT EXISTS input.cidade_sistema (
    sistema_id   text PRIMARY KEY,
    sistema_name text,
    cidade_id    text NOT NULL
        REFERENCES input.cidade(cidade_id)
);

-- aba do motor: sistema-topologia
-- PK em `componente_sistema_id` SOZINHO, e nao no par com o sistema: o motor indexa os nos
-- por id GLOBAL — `self.nos = {n.id: n for n in nos}` (otimizador_capex_v62.py:63). Um id
-- repetido em outro sistema seria aceito pelo banco e o motor manteria so o ultimo,
-- perdendo um no inteiro em silencio.
--
-- `sistema_id` ACEITA NULO: componente cadastrado e ainda nao colocado em sistema
-- nenhum. Do Databricks vem quais sub-bacias e qual ETE sao do sistema, e todas as
-- CTS — em que sistema cada CTS entra, e o caminho ate a ETE, quem monta e a
-- Regional. O motor pula essas linhas sozinho (`sistema_id not in sis_cid`).
CREATE TABLE IF NOT EXISTS input.sistema_topologia (
    componente_sistema_id         text PRIMARY KEY,
    componente_sistema_nome       text,
    sistema_id                    text
        REFERENCES input.cidade_sistema(sistema_id),
    componente_sistema_id_jusante text
);

-- ---- OPERACIONAL ----------------------------------------------------------
-- aba do motor: cidade-operacional
CREATE TABLE IF NOT EXISTS input.cidade_operacional (
    cidade_id          text PRIMARY KEY
        REFERENCES input.cidade(cidade_id),
    data_fim_concessao integer,
    unidade_cobertura  text
);
COMMENT ON COLUMN input.cidade_operacional.unidade_cobertura IS
    'ligacoes | economias | populacao. Define a REGUA da meta e da faixa de paridade '
    'daquela cidade. A receita continua sempre em ligacoes.';

-- aba do motor: subbacia-operacional
CREATE TABLE IF NOT EXISTS input.subbacia_operacional (
    sub_bacia                       text PRIMARY KEY,
    preco_por_ligacao               double precision,
    receita_faturada_media_mensal   double precision,
    receita_arrecadada_media_mensal double precision,
    tempo_arrecadacao               integer,
    tempo_ramp_up                   integer,
    vazao_contribuicao              double precision,
    universo_ligacoes               integer,
    ligacoes_atuais                 integer,
    ligacoes_novas_obras            integer,
    universo_economias              integer,
    economias_atuais                integer,
    economias_novas_obras           integer,
    universo_populacao              double precision,
    populacao_atual                 double precision,
    populacao_novas_obras           double precision,
    potencial_crescimento           double precision,
    universo_ligacoes_residencial   integer,
    ligacoes_atuais_residencial     integer,
    universo_economias_residencial  integer,
    economias_atuais_residencial    integer,
    -- O que a sub-bacia atende QUANDO A CTS NAO EXISTE: o exclusivo dela mais a area
    -- sobreposta com o coletor. So a sub-bacia tem estas colunas; a CTS nao precisa.
    universo_ligacoes_com_cts               integer,
    ligacoes_atuais_com_cts                 integer,
    universo_economias_com_cts              integer,
    economias_atuais_com_cts                integer,
    universo_ligacoes_residencial_com_cts   integer,
    ligacoes_atuais_residencial_com_cts     integer,
    universo_economias_residencial_com_cts  integer,
    economias_atuais_residencial_com_cts    integer
);
COMMENT ON COLUMN input.subbacia_operacional.universo_ligacoes_com_cts IS
    'O que a sub-bacia atende SEM a CTS: exclusivo dela + area sobreposta com o coletor. '
    'NAO e a soma das duas linhas — somar conta a sobreposicao duas vezes. Lida so quando '
    'a rodada tem usar_cts=false; com a CTS ligada, a sobreposicao esta nos numeros dela.';
COMMENT ON COLUMN input.subbacia_operacional.universo_ligacoes_residencial IS
    'PARCELA residencial, JA CONTIDA no total, e MEDIDA (nao derivada). As colunas sem '
    'sufixo sao o TOTAL = residencial + industrial. Nunca somar as duas.';
COMMENT ON COLUMN input.subbacia_operacional.ligacoes_atuais_residencial IS
    'Residenciais JA atendidas. Com COBERTURA_SO_RESIDENCIAL=True e a base da meta; o '
    'total continua sendo quem paga a receita.';
COMMENT ON COLUMN input.subbacia_operacional.universo_economias_residencial IS
    'Universo de economias residenciais. Usada quando a cidade mede cobertura em '
    'economias (input.cidade_operacional.unidade_cobertura).';
COMMENT ON COLUMN input.subbacia_operacional.economias_atuais_residencial IS
    'Economias residenciais ja atendidas. O RECORTE ACABA NA COBERTURA: receita, VPL, '
    'vazao e CAPEX seguem no total em qualquer modo. Cidade que mede em POPULACAO ignora '
    'estas colunas — industria nao mora, entao a populacao ja e residencial.';

-- aba do motor: componentes-subbacias-capex
CREATE TABLE IF NOT EXISTS input.componentes_subbacias_capex (
    sub_bacia            text NOT NULL
        REFERENCES input.subbacia_operacional(sub_bacia),
    componente           text NOT NULL,
    quantidade           double precision,
    unidade              text,
    preco_unitario       double precision,
    capex                double precision,
    opex                 double precision,
    tempo_predecessoras  integer,
    tempo_execucao       integer,
    obra_obrigatoria_ano integer,
    obra_proibida_ate    integer,
    wacc                 double precision,
    PRIMARY KEY (sub_bacia, componente)
);
COMMENT ON COLUMN input.componentes_subbacias_capex.obra_obrigatoria_ano IS
    '0 = nao e obrigatoria; -1 = obrigatoria em qualquer ano; AAAA = obrigatoria naquele ano.';
COMMENT ON COLUMN input.componentes_subbacias_capex.obra_proibida_ate IS
    '0 = sem restricao; AAAA = ano ate o qual a obra nao pode comecar.';
COMMENT ON COLUMN input.componentes_subbacias_capex.capex IS
    'CAPEX 0 com tempo_execucao > 0 = obra de TERCEIROS: entra no cronograma, nao no orcamento.';

-- aba do motor: ete-capex
CREATE TABLE IF NOT EXISTS input.ete_capex (
    ete_id                   text PRIMARY KEY,
    capacidade_por_modulo    double precision,
    -- A unidade em que `capacidade_por_modulo` e as demais capacidades desta ETE estao
    -- expressas. NAO e fixa no codigo de proposito: trocar a unidade de medida e mudanca
    -- de cadastro, e a soma nao muda com ela — so a leitura do numero. Vazia = a tela
    -- mostra a quantidade sem sufixo, em vez de inventar uma unidade.
    unidade_capacidade       text,
    capex_por_modulo         double precision,
    opex_por_modulo          double precision,
    tempo_predecessoras      integer,
    tempo_de_execucao        integer,
    capacidade_nominal_atual double precision,
    vazao_de_operacao_atual  double precision,
    capacidade_ociosa        double precision,
    obra_obrigatoria_ano     integer,
    obra_proibida_ate        integer,
    nova                     text,
    capex_terreno            double precision,
    modulos                  integer,
    wacc                     double precision
);

-- aba do motor: regional-operacional
CREATE TABLE IF NOT EXISTS input.regional_operacional (
    regional_id text PRIMARY KEY,
    ano_base    integer
);

-- aba do motor: orcamento
-- FALLBACK do teto de CAPEX quando ORCAMENTO nao vem no run_request. Sem esta tabela
-- E sem o parametro, o motor usa INF: no caminho do solver isso estoura la dentro do
-- CP-SAT com "OverflowError: cannot convert float infinity to integer".
-- A CHAVE E (regional_id, ano), e nao so `regional_id`: o teto de CAPEX e por ANO, e uma
-- chave sem ano so consegue guardar um valor por regional — o cronograma inteiro caberia
-- numa linha so, e a ultima gravacao apagaria as outras.
CREATE TABLE IF NOT EXISTS input.orcamento (
    regional_id text NOT NULL,
    ano         integer NOT NULL,
    valor_ano   double precision NOT NULL,
    PRIMARY KEY (regional_id, ano)
);

-- ---- METAS E PARIDADE -----------------------------------------------------
-- Sem FK para cidade de proposito: metas e faixas costumam ser carregadas antes do
-- cadastro completo da cidade. O indice cobre o acesso do motor.
-- aba do motor: metas-cobertura
CREATE TABLE IF NOT EXISTS input.metas_cobertura (
    cidade_id     text NOT NULL,
    ano           integer NOT NULL,
    cobertura_pct double precision,
    PRIMARY KEY (cidade_id, ano)
);

-- aba do motor: fator-esgoto
CREATE TABLE IF NOT EXISTS input.fator_esgoto (
    cidade_id     text NOT NULL,
    cidade_name   text,
    cobertura_pct double precision NOT NULL,
    paridade      double precision,
    PRIMARY KEY (cidade_id, cobertura_pct)
);
COMMENT ON TABLE input.fator_esgoto IS
    'PARIDADE esgoto/agua: tarifa_esgoto = ticket_medio(agua) x paridade. Vale a paridade '
    'da MAIOR faixa cuja cobertura_pct <= cobertura da cidade no ano (cobertura REALIZADA '
    'do plano). Cidade com paridade constante: uma unica linha com cobertura_pct = 0.';

-- ---- CTS (opcional: so existe se a unidade tiver Coletor de Tempo Seco) ----
-- aba do motor: cts-operacional
CREATE TABLE IF NOT EXISTS input.cts_operacional (
    cts                             text PRIMARY KEY,
    preco_por_ligacao               double precision,
    receita_faturada_media_mensal   double precision,
    receita_arrecadada_media_mensal double precision,
    tempo_arrecadacao               integer,
    tempo_ramp_up                   integer,
    vazao_contribuicao              double precision,
    universo_ligacoes               integer,
    ligacoes_atuais                 integer,
    ligacoes_novas_obras            integer,
    universo_economias              integer,
    economias_atuais                integer,
    economias_novas_obras           integer,
    universo_populacao              double precision,
    populacao_atual                 double precision,
    populacao_novas_obras           double precision,
    potencial_crescimento           double precision,
    universo_ligacoes_residencial   integer,
    ligacoes_atuais_residencial     integer,
    universo_economias_residencial  integer,
    economias_atuais_residencial    integer
);

-- aba do motor: subbacia-cts  (pareamento 1:1)
CREATE TABLE IF NOT EXISTS input.subbacia_cts (
    sub_bacia text PRIMARY KEY
        REFERENCES input.subbacia_operacional(sub_bacia),
    cts       text NOT NULL
        REFERENCES input.cts_operacional(cts)
);

-- aba do motor: componentes-cts-capex
CREATE TABLE IF NOT EXISTS input.componentes_cts_capex (
    cts                  text NOT NULL
        REFERENCES input.cts_operacional(cts),
    componente           text NOT NULL,
    quantidade           double precision,
    unidade              text,
    preco_unitario       double precision,
    capex                double precision,
    opex                 double precision,
    tempo_predecessoras  integer,
    tempo_execucao       integer,
    obra_obrigatoria_ano integer,
    obra_proibida_ate    integer,
    wacc                 double precision,
    PRIMARY KEY (cts, componente)
);

-- ---- INDICES que o backend/front consultam --------------------------------
CREATE INDEX IF NOT EXISTS ix_unidade_regional ON input.unidade_regional (regional_id);
CREATE INDEX IF NOT EXISTS ix_empresa_unidade  ON input.empresa (unidade_id);
CREATE INDEX IF NOT EXISTS ix_cidade_empresa   ON input.cidade_empresa (emp_codigo);
CREATE INDEX IF NOT EXISTS ix_sistema_cidade   ON input.cidade_sistema (cidade_id);
CREATE INDEX IF NOT EXISTS ix_topo_sistema     ON input.sistema_topologia (sistema_id);
CREATE INDEX IF NOT EXISTS ix_topo_jusante     ON input.sistema_topologia (componente_sistema_id_jusante);
CREATE INDEX IF NOT EXISTS ix_metas_cidade     ON input.metas_cobertura (cidade_id);
CREATE INDEX IF NOT EXISTS ix_fator_cidade     ON input.fator_esgoto (cidade_id);

-- ---- CONTROLE -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS controle.run_request (
    run_id        text PRIMARY KEY,
    unidade       text,
    params        jsonb NOT NULL,          -- os parametros da celula PARAMETROS
    solicitado_por text,
    solicitado_em timestamptz DEFAULT now()
);
COMMENT ON COLUMN controle.run_request.params IS
    'Chaves aceitas: job_databricks.MAPA_PARAMS + CHAVES_DO_JOB. Chave desconhecida e ERRO '
    '(nao silencio). Chave AUSENTE usa o default do ler_banco — o job nao inventa default '
    'proprio. E preciso teto ANUAL: ORCAMENTO no params ou input.orcamento — '
    'ORCAMENTO_TOTAL sozinho so limita o total da janela, nao o ano.';

CREATE TABLE IF NOT EXISTS controle.run_status (
    run_id     text PRIMARY KEY REFERENCES controle.run_request(run_id),
    status     text NOT NULL
        CHECK (status IN ('PENDENTE','RODANDO','SUCESSO','FALHOU_QUALIDADE','ERRO')),
    erro       text,
    atualizado_em timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS controle.run_diagnostico (
    run_id    text,
    checagem  text,
    nivel     text,
    ok        boolean,
    detalhe   text,
    gravado_em timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_diag_run ON controle.run_diagnostico(run_id);
