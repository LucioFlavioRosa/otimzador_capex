# 6. Dicionário de dados — schema de saída (`public.otim_*`)

Público: backend e front, que leem estas tabelas; e quem for validar uma rodada no banco.

258 colunas em 14 tabelas + 3 views. O DDL correspondente está em
[`ddl_resultado.sql`](../otimizador/infraestrutura/sql/ddl_resultado.sql) e é **gerado**, não escrito à mão — ver §6.10.

**Toda tabela tem `run_id`**, e toda tabela de detalhe tem FK para `otim_meta` com
`ON DELETE CASCADE`. Filtrar por `run_id` é obrigatório em qualquer consulta: o schema guarda
o histórico inteiro, não só a última rodada.

Convenções: valores monetários em **R$**; vazões na unidade do cadastro; `*_pct` em
**percentual** (0–100) salvo onde indicado; meses são **índices inteiros a partir do início do
plano** (`mes_indice = 0` é o primeiro mês), e as colunas `data_*` trazem a data legível
correspondente, em texto.

---

## 6.1 `otim_meta` — 1 linha por rodada · PK `run_id`

O cabeçalho. É o que a tela de histórico consome (via `otim_vw_historico`).

**Identificação e reprodutibilidade**

| Coluna | Tipo | Significado |
|---|---|---|
| `run_id` | TEXT | o id escolhido pelo backend; liga `controle.*` a `public.otim_*` |
| `data_hora` | TIMESTAMPTZ | quando a rodada foi materializada |
| `engine` / `engine_arquivo` / `engine_md5` | TEXT | módulo, caminho e hash do motor que rodou |
| `banco_arquivo` / `banco_md5` | TEXT | origem do cadastro e seu hash (no job: `postgres://input`) |
| `params_extra` | JSONB | **o `params` da `run_request`, como veio** — é aqui que se compara duas rodadas |
| `rotulo` / `usuario` | TEXT | rótulo da rodada e quem pediu |
| `blob_uri` | TEXT | **raiz** da cópia integral em parquet, quando o job roda com `blob` configurado; nulo caso contrário. Ver a nota abaixo sobre o formato |

> **Como navegar o `blob_uri`.** A gravação usa **uma pasta por tabela**, particionada por
> rodada: `<blob_uri>/<tabela>/run_id=<run_id>/`. Não existe uma pasta única com a rodada
> inteira — por isso a coluna guarda a raiz, e o `run_id` vem na mesma linha de `otim_meta`.
> A cópia congelada do cadastro está em `<blob_uri>/snapshot__*/run_id=<run_id>/`.
>
> ⚠️ **Rodadas publicadas antes de 2026-08-04** têm nesta coluna o valor antigo,
> `<raiz>/run_id=<run_id>` — um caminho que nunca existiu (a gravação sempre foi a de cima).
> Os dados dessas rodadas estão íntegros na raiz; só o ponteiro está errado. Para corrigir o
> histórico, se algum dia houver: `UPDATE public.otim_meta SET blob_uri =
> regexp_replace(blob_uri, '/run_id=[^/]+$', '') WHERE blob_uri LIKE '%/run_id=%';`

**Parâmetros efetivos da rodada**

| Coluna | Tipo | Significado |
|---|---|---|
| `regional` | TEXT | recorte da rodada |
| `anos_horizonte` / `anos_capex` / `ano_base` | BIGINT | horizonte total, janela de investimento, ano inicial |
| `ete_faseada` | BOOLEAN | ETE dividida em módulos-obra priorizáveis |
| `curva_adocao` | TEXT | ritmo de adesão das ligações novas |
| `foco_cobertura` | DOUBLE PRECISION | 0 = só VPL · 1 = só cobertura; nulo quando se usou `peso_cobertura` direto |
| `peso_cobertura` | DOUBLE PRECISION | peso em R$/ligação (derivado de `foco_cobertura`, ou informado) |
| `penalidade_cobertura` | TEXT | como a meta não atingida é penalizada |
| `peso_cidade` | JSONB | pesos por cidade |
| `orcamento_por_ano` | JSONB | teto de CAPEX por ano, como aplicado |
| `orcamento_total` | DOUBLE PRECISION | teto total da janela (nulo se não usado) |

**Solver**

| Coluna | Tipo | Significado |
|---|---|---|
| `milp_status` | TEXT | `OTIMO`, `VIAVEL(limite de tempo)` ou `SEM SOLUCAO(...)`, com sufixos (`\| OBRIG 3/3`). **Não** é `OPTIMAL`/`FEASIBLE` |
| `milp_solver` | TEXT | solver usado |
| `milp_bound` | DOUBLE PRECISION | limite dual (distância do ótimo, quando parou no tempo) |
| `tempo_s` | DOUBLE PRECISION | duração da otimização |

**Resultado consolidado**

| Coluna | Tipo | Significado |
|---|---|---|
| `vpl` | DOUBLE PRECISION | VPL do plano — **reconcilia com `SUM(otim_subbacia.vpl)`** (o portão verifica) |
| `vpl_obj` | DOUBLE PRECISION | valor da função objetivo (inclui o termo de cobertura) |
| `vp_efeito_base` | DOUBLE PRECISION | valor presente do efeito sobre a base existente |
| `capex_total` / `opex_total` / `receita_total` | DOUBLE PRECISION | totais; `capex_total` reconcilia com `SUM(otim_ano.capex)` |
| `obras_total` / `obras_construidas` | BIGINT | universo e o que entrou no plano |
| `obrig_total` / `obrig_construidas` | BIGINT | idem, só obrigatórias |
| `obrig_desconsideradas` | JSONB | obrigatórias que o modelo teve de largar (e por quê) |
| `subbacias_total` / `subbacias_faturando` | BIGINT | quantas passam a faturar |
| `metas_total` / `metas_nao_atingidas` | BIGINT | metas de cobertura |
| `deficit_cobertura` | DOUBLE PRECISION | déficit agregado (o portão exige ≥ 0) |
| `cobertura_final_pct` | DOUBLE PRECISION | cobertura ponderada pelo universo no fim do horizonte |
| `auditoria_ok` / `auditoria_reparos` | BOOLEAN / BIGINT | auditoria do teto: passou? quantos reparos |
| `aviso_orcamento` / `aviso_obrigatoria` | TEXT | avisos emitidos na carga |
| `status_execucao` | TEXT | status **da publicação** (`CONCLUIDO`), não o do ciclo do job — esse é `controle.run_status` |
| `erro` | TEXT | mensagem, quando houve |

---

## 6.2 `otim_obra` — 1 linha por obra · PK `(run_id, obra_id)`

A tabela mais consultada. Traz a decisão **e a explicação dela**.

**Identidade e localização**

`obra_id` · `tipo` (coleta, rede, transporte, ete, ete_mod…) · `componente` (lig, rede, tro,
eee, lr, cts, ete, ete_mod) · **`no`** (a sub-bacia ou CTS a que pertence — é o nome da coluna
no banco; a view `otim_vw_obra_fora` a expõe como `sub_bacia`) · `sistema` · `cidade` ·
`regional` · `is_cts` (BOOLEAN) · `responsavel` (**Aegea ou terceiros**) · `necessaria`.

**Custo**

`capex` · `capex_componentes` (JSONB, a composição) · `quantidade` · `unidade` ·
`preco_unitario` (CAPEX = quantidade × preço) · `opex_ano` · `wacc` · **`wacc_origem`** (se veio
da obra ou herdou o `wacc_medio` da unidade).

> **Obra de terceiros:** `capex = 0` com `prazo_meses > 0`. Ela acontece e libera a cadeia, mas
> não consome orçamento. `responsavel` é a forma explícita de filtrar.

**Prazos e janelas**

`prazo_meses` · `prazo_inicio_meses` · `inicio_min_mes` · `obrigatoria` (BOOLEAN) ·
`obrig_ano_plano` (ano em que a obrigatória foi encaixada) · `proibida_ate` ·
`proibida_nunca`.

**Receita associada**

`ligacoes` · `ticket_mes` · `preco_ligacao` · `arrec_dir` / `arrec_ind` (arrecadação direta e
indireta) · `lag_meses` · `maturacao_meses`.

**Decisão**

| Coluna | Tipo | Significado |
|---|---|---|
| `construida` | BOOLEAN | entrou no plano |
| `mes_inicio` / `data_inicio` | BIGINT / TEXT | quando começa |
| `mes_pronta` / `data_pronta` | BIGINT / TEXT | quando fica pronta |
| `faturando` | BOOLEAN | a obra destrava faturamento (só faz sentido em obra de coleta; nulo nas demais) |
| `mes_inicio_faturamento` / `data_inicio_faturamento` | BIGINT / TEXT | quando a receita começa |
| `status` | TEXT | `FORA` é o valor que a view `otim_vw_obra_fora` filtra |

**Explicação (é o diferencial do modelo)**

| Coluna | Significado |
|---|---|
| `categoria_motivo` | classe do motivo pelo qual ficou de fora |
| `motivo` | o motivo em texto |
| `elo_que_trava` | **qual obra da cadeia impede esta de faturar** |
| `saldo_potencial` | quanto valeria se o elo fosse destravado |

---

## 6.3 `otim_subbacia` — 1 linha por sub-bacia/CTS · PK `(run_id, sub_bacia)`

**Identidade:** `sub_bacia` · `cidade` · `sistema` · `regional` · `jusante` (próximo nó da
cadeia) · `is_cts` · `tipo_estrutura` · `latitude` / `longitude`.

**Base comercial:** `ligacoes_atuais` · `ligacoes_novas` · `ticket_medio` · `arrecadacao` ·
`vazao_marginal` · `potencial_crescimento` · `wacc_receita` · `horizonte_anos`.

**Régua de cobertura:** `unidade_cobertura` (`ligacoes` | `economias` | `populacao`) —
o parâmetro `UNIDADE_COBERTURA` da rodada, repetido em cada linha para o resultado ser
lido sozinho · `fator_unidade_cobertura` e `unid_fator_cobertura` (conversão para a régua) ·
`densidade_economias` · `densidade_populacao`.

**Faturamento:** `faturando` (BOOLEAN) · `obra_coleta` (a obra âncora) ·
`mes_inicio_faturamento` / `data_inicio_faturamento` · **`motivo_sem_receita`** (por que não
fatura).

**VPL decomposto** — a soma de `vpl` sobre todas as sub-bacias reconcilia com `otim_meta.vpl`:

`vpl` · `vp_receita_direta` · `vp_receita_indireta` · `vp_efeito_base` · `vp_capex_rateado` ·
`vp_opex_rateado`.

**Potencial não realizado** (o que ganharia se as obras faltantes fossem feitas):
`pot_vp_receita` · `pot_vp_capex_solo` · `pot_vp_capex_rateado` · `pot_vp_opex` ·
`pot_saldo_solo` · `pot_saldo_rateado` · `pot_obras_faltantes` (BIGINT, contagem).

---

## 6.4 `otim_subbacia_ano` — curvas · PK `(run_id, sub_bacia, ano)`

`receita_direta` · `receita_indireta` · `efeito_base` · `capex_rateado` · `opex_rateado` ·
`ebitda` · `faturando` (BOOLEAN), mais `cidade` e `sistema` desnormalizados para evitar join.

É a tabela dos gráficos de evolução por sub-bacia.

---

## 6.5 `otim_sistema` — ETE e capacidade · PK `(run_id, sistema)`

| Coluna | Tipo | Significado |
|---|---|---|
| `cidade`, `horizonte_anos`, `ano_fim_concessao` | — | horizonte do sistema = fim da concessão da cidade |
| `sub_bacias` / `sub_bacias_faturando` | BIGINT | quantas atende e quantas faturam |
| `ete_id` / `ete_responsavel` | TEXT | a ETE do sistema |
| `ete_nova` | BOOLEAN | ETE nova × ampliação de existente. Declarada em `TIPOS_FIXOS`: as fixtures não têm ETE, então seria inferida TEXT e o front compararia string |
| `capacidade_modulo` / `capex_modulo` / `capex_terreno` | DOUBLE PRECISION | economia de um módulo |
| `unidade_capacidade` | TEXT | a unidade em que as capacidades desta ETE estão, **congelada nesta rodada**. Vem de `input.ete_capex.unidade_capacidade`: trocar a medida é mudança de cadastro, e uma rodada antiga continua dizendo a que ela usou. Nula em rodada publicada antes da coluna existir — a tela mostra o número sem sufixo |
| `modulos_disponiveis` / `modulos_construidos` | BIGINT | universo × plano |
| `capex_modulos_construidos` | DOUBLE PRECISION | soma do CAPEX dos módulos que entraram |
| `folga_inicial` / `capacidade_instalada` / `folga_remanescente` | DOUBLE PRECISION | capacidade antes, depois e sobra |
| `vazao_conectada` / `vazao_total_sistema` / `vazao_nao_atendida` | DOUBLE PRECISION | vazão ligada, potencial e o que não coube |
| `ocupacao_pct` | DOUBLE PRECISION | `vazao_conectada / capacidade_instalada × 100` |
| `primeiro_modulo_mes` | BIGINT | mês do primeiro módulo |

---

## 6.6 `otim_dependencia` — arestas obra ↔ sub-bacia · PK `(run_id, obra_id, sub_bacia)`

O grafo do rateio. Uma obra de transporte serve várias sub-bacias, e o CAPEX é dividido na
proporção da vazão.

`vazao_sub_bacia` · `vazao_total_obra` · **`fracao_rateio`** · `capex_rateado` ·
`n_dependentes` · `obra_construida` · `sub_bacia_faturando`.

> **Invariante:** `SUM(fracao_rateio) = 1` para cada `obra_id` (o portão verifica com desvio
> < 1e-6). Se você somar `capex_rateado` por obra, tem de dar o `capex` dela em `otim_obra`.

A view `otim_vw_topologia` já entrega estas linhas com os atributos da obra anexados.

---

## 6.7 Tabelas por ano

**`otim_ano`** · PK `(run_id, ano)` — o painel financeiro:
`ano_indice` · `capex` · `opex` · `receita` · `receita_efeito_base` · `receita_total` ·
`ebitda` · `ebitda_acumulado` · `ebitda_margem_pct` · **`teto_capex`** · `uso_teto_pct` ·
`excesso` (quanto passou do teto; o portão exige ≈ 0) · `dentro_janela_capex`.

**`otim_mes`** · PK `(run_id, mes_indice)` — a curva S: `ano` · `mes` · `competencia` (texto
legível) · `capex_mes` · `capex_acumulado`. `SUM(capex_mes)` reconcilia com `SUM(otim_ano.capex)`.

**`otim_cidade_ano`** · PK `(run_id, cidade, ano)` — só `capex`. Existe para o gráfico de CAPEX
por cidade sem varrer `otim_obra`.

---

## 6.8 Cobertura, metas e paridade

**`otim_cidade`** · PK `(run_id, cidade)` — consolidado:
`sub_bacias` · `obras_feitas` / `obras_fora` · `capex_total` · `vpl` · `ligacoes_novas` ·
`universo` · `base_atendida` · `cobertura_base_pct` (antes) · `cobertura_final_pct` (depois) ·
`metas_total` / `metas_atingidas` · `paridade_inicial` / `paridade_final` · `peso_cidade` ·
`unidade_cobertura` (a régua da rodada).

**`otim_cobertura`** · PK `(run_id, cidade, ano)` — cobertura realizada ano a ano:
`ligacoes_cobertas` · `universo` · `cobertura_pct`.

**`otim_meta_cobertura`** · PK `(run_id, cidade, ano)` — meta a meta:
`pct_alvo` · `alvo_ligacoes` · `cobertura_ligacoes` · **`deficit_ligacoes`** (o portão exige
≥ 0) · `atingida` (BOOLEAN) · `dentro_janela_capex`.

**`otim_paridade`** · PK `(run_id, cidade, ano)` — a paridade esgoto/água aplicada:
`paridade` · `paridade_base` · `delta_paridade`. É **endógena**: depende da cobertura que o
próprio plano realizou naquele ano.

---

## 6.9 `otim_auditoria` — estouros e reparos · **sem PK**

`tipo` · `ano` · `gasto` · `teto` · `excesso` · `detalhe` (JSONB). Fica **vazia numa rodada
saudável**. Não tem chave natural (é um log de violações + reparos), mas tem FK para
`otim_meta`, então some junto na republicação.

---

## 6.10 O DDL é gerado — e por quê importa

`otimizador/infraestrutura/sql/ddl_resultado.sql` sai de `python main.py gerar-ddl`, que chama
`publicacao.ddl_postgres(tabs)`. **Não edite o `.sql` à mão:** um esquema divergente do que a
publicação escreve ou faz o `INSERT` falhar com erro obscuro, ou — pior — aceita número em
coluna `TEXT` e quebra `ORDER BY`/`SUM`/gráfico no front, sem erro nenhum.

O tipo de cada coluna é **inferido do dtype** do DataFrame materializado. Coluna que esteja
toda nula na rodada usada como amostra não tem dtype útil e cairia em `TEXT` — por isso o
gerador materializa **cinco cenários** (com CTS, com ETE faseada, sem CTS com mix de WACC, e os
dois lados do recorte `cobertura_so_residencial`) e fica com o tipo mais específico que qualquer
um revelou. O que
mesmo assim nunca aparece está declarado à mão em `publicacao.TIPOS_FIXOS`, com o tipo tirado
de quem escreve a coluna em `persistencia.py`.

Se você adicionar coluna à materialização: rode o gerador, confira o aviso que ele imprime
sobre colunas que ficaram `TEXT` e nulas, e aplique o `.sql` como migration. `CREATE TABLE IF
NOT EXISTS` **não** adiciona coluna a tabela existente — para uma coluna nova é preciso
`ALTER TABLE`.

---

## 6.11 Consultas de validação

Úteis ao subir o banco pela primeira vez — são as mesmas reconciliações do portão de qualidade,
agora contra o que está gravado:

```sql
-- 1) uma rodada = uma linha de cabeçalho
SELECT count(*) FROM public.otim_meta WHERE run_id = :run;                     -- 1

-- 2) VPL do plano = soma por sub-bacia
SELECT m.vpl - (SELECT sum(vpl) FROM public.otim_subbacia WHERE run_id = :run) AS diff
  FROM public.otim_meta m WHERE m.run_id = :run;                               -- ~0

-- 3) CAPEX: cabeçalho = ano = mês = cidade/ano
SELECT (SELECT capex_total       FROM public.otim_meta        WHERE run_id = :run) AS meta,
       (SELECT sum(capex)        FROM public.otim_ano         WHERE run_id = :run) AS por_ano,
       (SELECT sum(capex_mes)    FROM public.otim_mes         WHERE run_id = :run) AS por_mes,
       (SELECT sum(capex)        FROM public.otim_cidade_ano  WHERE run_id = :run) AS por_cidade;

-- 4) rateio: cada obra soma 1
SELECT obra_id, sum(fracao_rateio) FROM public.otim_dependencia
 WHERE run_id = :run GROUP BY 1 HAVING abs(sum(fracao_rateio) - 1) > 1e-6;     -- vazio

-- 5) teto respeitado e definido
SELECT ano, capex, teto_capex, excesso FROM public.otim_ano
 WHERE run_id = :run AND (excesso > 1 OR teto_capex IS NULL) ORDER BY ano;     -- vazio

-- 6) republicação não duplicou
SELECT run_id, count(*) FROM public.otim_meta GROUP BY 1 HAVING count(*) > 1;  -- vazio
```

---

Voltar ao [índice](README.md) · contrato de leitura por tela em
[`02-integracao-backend.md`](02-integracao-backend.md) §2.5.
