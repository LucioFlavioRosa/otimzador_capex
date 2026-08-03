# 2. Integração com o backend

Público: quem escreve o backend (AKS) e o front do cadastro. Este documento é o **contrato**.

O backend **nunca empurra nem puxa linhas por API**. Ele escreve no Postgres, dispara o job, e
depois lê o resultado do Postgres. O job é o dono de todo o I/O pesado.

```
front ──escreve──▶ input.*            (cadastro)
backend ──insere─▶ controle.run_request  (parâmetros)   ──dispara──▶ Databricks Job
backend ──lê─────▶ controle.run_status   (acompanhamento)
backend ──lê─────▶ public.otim_* / views (resultado)
```

---

## 2.1 Divisão de responsabilidade e permissões

| Schema | front | backend | job |
|---|---|---|---|
| `input` | **INSERT/UPDATE/DELETE** | SELECT | SELECT |
| `controle.run_request` | — | **INSERT** | SELECT |
| `controle.run_status` / `run_diagnostico` | — | SELECT | **INSERT/UPDATE/DELETE** |
| `public.otim_*` | SELECT | SELECT | **ALL** |

Nem o front nem o backend escrevem em `public`. Se aparecer necessidade disso, é sinal de que
algo está sendo calculado no lugar errado.

---

## 2.2 Passo 1 — o cadastro (`input`)

16 tabelas, criadas por `otimizador/infraestrutura/sql/ddl_input.sql`. Os nomes de coluna são **lidos de forma estrita** pelo
motor: não há fallback para nome antigo (há um teste que garante isso). Renomear coluna quebra
a leitura em silêncio — a aba simplesmente vem sem aquele campo.

### Hierarquia (a espinha dorsal)

```
unidade_regional ─▶ regional_superintendencia ─▶ superintendencia_cidade ─▶ cidade_sistema ─▶ sistema_topologia
```

Todas com FK. Um elo quebrado produz **sub-bacia órfã**, que some do resultado sem erro — por
isso as FKs existem.

### Tabelas e chaves

| Tabela | PK | Observação |
|---|---|---|
| `unidade_regional` | `unidade_id` | `wacc_medio` é o WACC herdado por obra sem WACC próprio |
| `regional_superintendencia` | `superintendencia_id` | FK → unidade |
| `superintendencia_cidade` | `cidade_id` | FK → superintendência |
| `cidade_sistema` | `sistema_id` | FK → cidade |
| `sistema_topologia` | `componente_sistema_id` | id de nó é **global** (o motor indexa por ele): não repita o mesmo id em dois sistemas. `componente_sistema_id_jusante` monta a cadeia até a ETE |
| `cidade_operacional` | `cidade_id` | `unidade_cobertura` = régua da cidade; `data_fim_concessao` = horizonte |
| `subbacia_operacional` | `sub_bacia` | o cadastro econômico da sub-bacia |
| `componentes_subbacias_capex` | `(sub_bacia, componente)` | uma linha = uma obra possível |
| `ete_capex` | `ete_id` | módulos, capacidade, capex de terreno |
| `regional_operacional` | `regional_id` | `ano_base` |
| `orcamento` | `regional_id` | **fallback** do teto quando `ORCAMENTO` não vem no `run_request` |
| `metas_cobertura` | `(cidade_id, ano)` | `cobertura_pct` é `double` — meta pode ter casa decimal |
| `fator_esgoto` | `(cidade_id, cobertura_pct)` | faixas de paridade |
| `cts_operacional` | `cts` | opcional |
| `subbacia_cts` | `sub_bacia` | pareamento 1:1, opcional |
| `componentes_cts_capex` | `(cts, componente)` | opcional |

### Três armadilhas do cadastro

**(a) Duplicata corrompe o plano em silêncio.** É o motivo das PKs. Sem elas: em
`subbacia_operacional` a última linha vence (some uma sub-bacia); em
`componentes_*_capex` a obra é **duplicada** e o CAPEX conta duas vezes — e isso **passa em
todas as reconciliações** do portão de qualidade, porque o resultado é internamente coerente
com um cadastro que de fato tem duas obras.

**(b) A parcela industrial já está no total.**

| coluna | leitura |
|---|---|
| `universo_ligacoes` = 1000 | **total** (residencial + industrial) |
| `universo_ligacoes_industrial` = 80 | **parcela**, já contida nos 1000 |

`INCLUIR_INDUSTRIAL=True` → usa 1000. `False` → usa 1000 − 80 = 920. **Nunca somar.** Vale
igual para `receita_faturada_*`, `receita_arrecadada_*`, `ligacoes_atuais_*` e
`vazao_contribuicao_*`.

**(c) Colunas derivadas são recalculadas.** `ligacoes_novas_obras = universo − atuais` é
**derivado pelo motor**; o valor gravado no banco é ignorado. Não adianta "corrigir" na mão.

### Códigos das janelas de obra

| Coluna | `0` | `-1` | `AAAA` |
|---|---|---|---|
| `obra_obrigatoria_ano` | não é obrigatória | obrigatória em qualquer ano | obrigatória naquele ano exato |
| `obra_proibida_ate` | sem restrição | — | não pode começar até aquele ano |

E: **CAPEX 0 com `tempo_execucao` > 0 = obra de terceiros** — entra no cronograma e libera a
cadeia, mas não consome orçamento.

---

## 2.3 Passo 2 — disparar a rodada (`controle.run_request`)

```sql
INSERT INTO controle.run_request (run_id, unidade, params, solicitado_por)
VALUES (
  'run_2026_07_27_capex_base_01',          -- o backend escolhe; é a chave de tudo
  'u1',
  '{"UNIDADE": "u1",
    "ORCAMENTO": {"2026": 50000000, "2027": 50000000, "2028": 40000000},
    "BASE_RECEITA": "arrecadada",
    "USAR_CTS": true,
    "INCLUIR_INDUSTRIAL": true,
    "FOCO_COBERTURA": 0.3,
    "USUARIO": "fulano@aegea.com.br"}'::jsonb,
  'backend-aks'
);
```

Depois: dispara o job pela **Databricks Jobs API** passando `run_id` como widget, **ou**
publica numa fila **Service Bus** que o job consome. A fila desacopla o backend da
disponibilidade do cluster.

### O `run_id` é seu

Escolha um identificador estável e legível. Ele atravessa os três schemas:
`controle.run_status.run_id` = `controle.run_diagnostico.run_id` = `public.otim_meta.run_id`.
É a única chave que correlaciona requisição, diagnóstico e resultado.

### Contrato do `params` (JSONB)

Três regras, validadas em `job_databricks._params_para_ler_banco`:

1. **Chave desconhecida é ERRO**, não silêncio. Um `orcamento` minúsculo faria a rodada sair
   sem teto de CAPEX; agora a rodada falha com a lista de chaves esperadas.
2. **Chave ausente usa o default do motor.** O job não inventa default próprio — senão o mesmo
   `params` daria planos diferentes no job e no notebook do analista.
3. **Tem de existir teto anual de CAPEX.** Ele vem de `ORCAMENTO` no `params` **ou** da
   tabela `input.orcamento`; a verificação acontece depois da carga, justamente para o
   fallback pela tabela valer. **`ORCAMENTO_TOTAL` sozinho não serve:** ele limita o total
   da janela, mas a restrição anual lê `cen.orc`, que ficaria infinito — e o CP-SAT estoura
   ao converter infinito em inteiro.

#### Chaves do motor

| Chave | Tipo | Default do motor | O que faz |
|---|---|---|---|
| `UNIDADE` | string | `None` (todas) | recorte da rodada |
| `REGIONAL` | string | `None` | recorte alternativo |
| `ORCAMENTO` | número, dict `{unidade: teto}` ou dict `{ano: teto}` | `None` | teto **anual** de CAPEX. Com `{ano: teto}`, o cronograma define a janela. O JSONB entrega a chave do ano como string (`"2026"`) e o job a converte para `int` — que é o único formato que o motor reconhece como cronograma |
| `ORCAMENTO_TOTAL` | número | `None` | teto **total** da janela; o otimizador distribui entre os anos. **Não substitui `ORCAMENTO`** — a restrição anual continua vindo dele |
| `HORIZONTE_CAPEX` | int | `None` | anos em que se pode investir |
| `ANOS_EXTRA_CONCLUSAO` | int | `3` | cauda para concluir o que começou |
| `DATA_INICIO` | data | `None` | início do cronograma |
| `BASE_RECEITA` | `"arrecadada"` \| `"faturada"` | `"arrecadada"` | base de receita da rodada |
| `CURVA_ADOCAO` | `"scurve"` \| … | `"scurve"` | ritmo de adesão das ligações novas |
| `USAR_CTS` | bool | `true` | CTS como nó próprio |
| `INCLUIR_INDUSTRIAL` | bool | `true` | `true` = usa o total; `false` = total − industrial |
| `ETE_FASEADA` | bool | **`false`** | cada ETE vira K obras-módulo |
| `ETE_FIXO` | bool | `false` | ETE fora da decisão |
| `METAS_COBERTURA` | dict | `None` | sobrescreve as metas do cadastro |
| `FOCO_COBERTURA` | float 0..1 | **`None`** | 0 = só VPL · 1 = só cobertura. Converte-se num peso auto-calibrado |
| `PESO_COBERTURA` | float | `0.0` | peso bruto em R$/ligação. **Só é usado se `FOCO_COBERTURA` for `null`** |
| `PENALIDADE_COBERTURA` | string | `"meta+cobertura"` | como a meta não atingida é penalizada |
| `PESO_CIDADE` | dict `{cidade: peso}` | `None` | prioriza cidades |

> ⚠️ **`FOCO_COBERTURA` e `ETE_FASEADA` são os dois parâmetros mais perigosos.**
> `FOCO_COBERTURA = 1.0` satura o peso de cobertura: o objetivo vira "cobertura a qualquer
> custo, VPL irrelevante" — o extremo oposto do default. `ETE_FASEADA = true` muda o conjunto
> de obras do modelo. Se você não sabe qual quer, **não mande a chave** e o motor aplica o
> default dele.

#### Chaves do job (não vão para o motor)

| Chave | Default | O que faz |
|---|---|---|
| `USUARIO` | `"job-databricks"` | vai para `otim_meta.usuario`, aparece no histórico |
| `MAX_TIME_S` | `300` | tempo máximo do solver, por rodada |
| `WORKERS` | `8` | threads do CP-SAT |

---

## 2.4 Passo 3 — acompanhar

```sql
SELECT status, erro, atualizado_em
FROM controle.run_status WHERE run_id = :run;
```

| Status | O que o backend deve fazer |
|---|---|
| `PENDENTE` / `RODANDO` | aguardar (ou mostrar progresso) |
| `SUCESSO` | ler `public.otim_*` |
| `FALHOU_QUALIDADE` | **não há resultado publicado.** Mostrar o diagnóstico ao usuário |
| `ERRO` | falha técnica. `run_status.erro` traz `TipoDaExcecao: mensagem` |

Por que a qualidade reprovou:

```sql
SELECT checagem, nivel, ok, detalhe
FROM controle.run_diagnostico
WHERE run_id = :run AND ok = false
ORDER BY nivel;   -- 'critico' antes de 'aviso'
```

O diagnóstico é gravado **sempre**, inclusive quando a rodada passa — é útil como registro de
saúde da rodada, não só de falha.

---

## 2.5 Passo 4 — ler o resultado

Uma consulta por tela, **sem join** onde possível. Este é o contrato que `leitor_v2.py`
reproduz e que `publicacao.contrato_backend()` imprime.

| Tela | Consulta |
|---|---|
| Histórico de otimizações | `SELECT * FROM public.otim_vw_historico LIMIT 50 OFFSET :n` |
| Cabeçalho de uma rodada | `SELECT * FROM public.otim_meta WHERE run_id = :run` |
| Painel geral (gráficos) | `SELECT * FROM public.otim_ano WHERE run_id = :run ORDER BY ano`<br>`SELECT * FROM public.otim_mes WHERE run_id = :run ORDER BY mes_indice` |
| Lista de obras | `SELECT * FROM public.otim_obra WHERE run_id = :run [AND cidade = :c] [AND status = :s]` |
| Obras fora + diagnóstico | `SELECT * FROM public.otim_vw_obra_fora WHERE run_id = :run` |
| Deep dive da sub-bacia | `SELECT * FROM public.otim_subbacia WHERE run_id = :run AND sub_bacia = :sb` |
| Topologia até a ETE | `SELECT * FROM public.otim_vw_topologia WHERE run_id = :run AND sub_bacia = :sb` |
| Visão da cidade | `SELECT * FROM public.otim_cidade WHERE run_id = :run`<br>`SELECT * FROM public.otim_cidade_ano WHERE run_id = :run AND cidade = :cid` |
| Cobertura no tempo | `SELECT * FROM public.otim_cobertura WHERE run_id = :run ORDER BY cidade, ano` |
| Metas | `SELECT * FROM public.otim_meta_cobertura WHERE run_id = :run` |
| Paridade | `SELECT * FROM public.otim_paridade WHERE run_id = :run` |
| Auditoria do teto | `SELECT * FROM public.otim_auditoria WHERE run_id = :run` |

### As 14 tabelas publicadas

| Tabela | PK | Ordem de grandeza | Conteúdo |
|---|---|---|---|
| `otim_meta` | `run_id` | **1 linha** | parâmetros, versões, status, totais da rodada |
| `otim_obra` | `run_id, obra_id` | ~1,7 mil | atributos + decisão + **motivo** de cada obra |
| `otim_subbacia` | `run_id, sub_bacia` | ~500 | VPL decomposto e potencial |
| `otim_subbacia_ano` | `run_id, sub_bacia, ano` | ~6 mil | curvas de receita/CAPEX/OPEX |
| `otim_dependencia` | `run_id, obra_id, sub_bacia` | ~5 mil | arestas obra→sub-bacia + fração de rateio |
| `otim_sistema` | `run_id, sistema` | ~100 | ETE, capacidade, folga, ocupação |
| `otim_ano` | `run_id, ano` | ~24 | CAPEX/OPEX/receita vs teto |
| `otim_mes` | `run_id, mes_indice` | ~288 | CAPEX mensal (curva S) |
| `otim_cidade` | `run_id, cidade` | ~14 | consolidado por cidade |
| `otim_cidade_ano` | `run_id, cidade, ano` | ~336 | CAPEX por cidade e ano |
| `otim_cobertura` | `run_id, cidade, ano` | ~336 | cobertura realizada |
| `otim_meta_cobertura` | `run_id, cidade, ano` | ~31 | alvo, realizado, déficit, atingida |
| `otim_paridade` | `run_id, cidade, ano` | ~336 | paridade aplicada |
| `otim_auditoria` | — | 0..n | anos com estouro de teto e reparos |

**Volume por rodada: ~7 mil linhas.** Pagine a lista de obras; o resto cabe inteiro na
memória do backend.

### Colunas que o front costuma pedir primeiro

`otim_meta` — `rotulo`, `usuario`, `data_hora`, `status_execucao`, `milp_status`, `vpl`,
`capex_total`, `obras_construidas`/`obras_total`, `subbacias_faturando`/`subbacias_total`,
`cobertura_final_pct`, `metas_total`/`metas_nao_atingidas`, `auditoria_ok`, `tempo_s`,
`orcamento_por_ano` (JSONB), `params_extra` (JSONB, o `params` da rodada).

`otim_obra` — `obra_id`, `tipo`, `componente`, `no` (a sub-bacia), `cidade`, `capex`,
`quantidade`/`unidade`/`preco_unitario`, `wacc` + `wacc_origem`, `data_inicio`, `data_pronta`,
`construida`, `status`, e o trio da explicação: `categoria_motivo`, `motivo`, `elo_que_trava`,
`saldo_potencial`. `responsavel` distingue obra da Aegea de obra de terceiros.

`otim_meta_cobertura` — `pct_alvo`, `alvo_ligacoes`, `cobertura_ligacoes`, `deficit_ligacoes`,
`atingida`.

### As 3 views

- **`otim_vw_historico`** — a tela inicial inteira, sem nenhum join, já ordenada por
  `data_hora DESC`.
- **`otim_vw_obra_fora`** — só as obras com `status = 'FORA'`, com o diagnóstico montado.
- **`otim_vw_topologia`** — arestas de `otim_dependencia` já enriquecidas com os atributos da
  obra (`capex`, `data_inicio`, `data_pronta`, `responsavel`, `status`).

---

## 2.6 Evento de conclusão

Publicado no Service Bus (ou webhook) **depois** do commit:

```json
{"evento": "otimizacao.concluida",
 "run_id": "run_2026_07_27_capex_base_01",
 "status": "CONCLUIDO",
 "vpl": 123456789.0,
 "capex_total": 98765432.0,
 "obras_construidas": 412,
 "blob_uri": "abfss://..."}
```

O backend só precisa **invalidar o cache da lista** — quando o evento chega, os dados já estão
commitados. Não é preciso reconsultar o status antes de ler.

---

## 2.7 Idempotência e retry — o que dá para assumir

**Reprocessar o mesmo `run_id` é seguro.** A publicação apaga a rodada anterior
(`DELETE FROM otim_meta WHERE run_id = ...`) e o `ON DELETE CASCADE` remove todos os detalhes,
antes de regravar. Vale também para o diagnóstico e o status (upsert).

Consequências para o backend:

- **Retry é livre.** Job que morreu no meio, cluster reiniciado, timeout de rede: basta
  disparar o mesmo `run_id` de novo.
- **Um `run_id` = uma rodada.** `otim_meta` nunca tem duas linhas com o mesmo `run_id`.
- **Publicar e marcar `SUCESSO` é uma transação só.** Não existe o estado "dados publicados
  mas status `RODANDO`". Se o commit falhar, nada foi publicado e o status não avançou.
- **Rodada nova exige `run_id` novo.** Reusar um `run_id` para outros parâmetros **apaga** o
  resultado anterior. Se o histórico importa, gere um id por execução.

---

## 2.8 Erros comuns

| Sintoma | Causa | Correção |
|---|---|---|
| `ERRO`: `ValueError: run_request.params com chaves desconhecidas: ['orcamento']` | chave fora do contrato (maiúsculas/acento) | use exatamente as chaves de §2.3 |
| `ERRO`: `sem teto anual de CAPEX para [...]` | nem `ORCAMENTO` no `params` nem linha em `input.orcamento` (ou só `ORCAMENTO_TOTAL`) | informe `ORCAMENTO`, ou preencha `input.orcamento` |
| `ERRO`: `run_request nao encontrada para run_id=...` | job disparado antes do `INSERT`, ou `run_id` diferente | inserir e **commitar** antes de disparar |
| `ERRO`: `falha ao ler input.<tabela>` | permissão, rede, ou tabela ausente que não é opcional | ver `03-producao.md` §7 |
| `ERRO`: `input incompleto no Postgres: falta 'subbacia_operacional'` | cadastro vazio para a unidade | conferir o carregamento do `input` |
| `FALHAU_QUALIDADE` com "duplicatas nas PKs" | cadastro duplicado gerou resultado duplicado | conferir `input` e o `run_diagnostico` |
| Plano vazio, mas `SUCESSO` | "Plano não-vazio" é **aviso**, não crítico | decisão de negócio: se plano vazio deve barrar, mudar o nível em `qualidade.py` |
| Resultado diferente do notebook com o mesmo cadastro | `params` diferente — tipicamente `FOCO_COBERTURA` ou `ETE_FASEADA` | comparar `otim_meta.params_extra` das duas rodadas |

---

Próximo: **`03-producao.md`** (instalar, configurar e operar).
