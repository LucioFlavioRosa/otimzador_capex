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

**(b) A parcela residencial já está no total.**

| coluna | leitura |
|---|---|
| `universo_ligacoes` = 1000 | **total** (residencial + industrial) |
| `universo_ligacoes_residencial` = 920 | **parcela**, já contida nos 1000 |

`COBERTURA_SO_RESIDENCIAL=False` (padrão) → a meta usa 1000. `True` → usa 920. **Nunca
somar.** As quatro colunas do recorte são `universo_ligacoes_residencial`,
`ligacoes_atuais_residencial`, `universo_economias_residencial` e
`economias_atuais_residencial`.

**O RECORTE ACABA NA COBERTURA.** Receita, VPL, vazão e CAPEX usam o total nos dois modos —
quem paga a conta é a ligação, seja de casa ou de fábrica, e a indústria manda esgoto que a
ETE precisa tratar. A versão anterior deste recorte (`INCLUIR_INDUSTRIAL`, com colunas
`*_industrial`) descontava a parcela de ligações, receita **e vazão**; ela não existe mais, e
as colunas foram removidas do DDL pela migração `ddl_input_migracao_02.sql`.

**(b2) A sub-bacia diz o que atende SEM a CTS.**

As colunas de ligação e economia da sub-bacia são o que pertence **exclusivamente** a ela. A
CTS cobre uma área que se **sobrepõe** a essa, e a sobreposição é contada **uma vez só**, na
entidade que a atende em cada cenário:

| rodada | quem atende a sobreposição | o que o motor lê na sub-bacia |
|---|---|---|
| `usar_cts=true` | a CTS (que entra como nó próprio, com as obras dela) | as colunas exclusivas |
| `usar_cts=false` | a sub-bacia (as obras da CTS ficam de fora) | as colunas `*_com_cts` |

São oito, e elas cruzam com o recorte residencial porque as duas escolhas são
independentes: `universo_ligacoes_com_cts`, `ligacoes_atuais_com_cts`,
`universo_economias_com_cts`, `economias_atuais_com_cts`, e as quatro
`*_residencial_com_cts` equivalentes.

**NÃO é a soma das duas linhas.** Era o que o motor fazia, e a ligação da área sobreposta,
que está nas duas, era contada duas vezes: o universo da meta crescia sozinho ao desligar a
CTS, e a cobertura piorava sem nenhuma obra ter mudado. O valor apurado vai ser **menor** que
a soma onde houver sobreposição real.

**Ligado e desligado deixam de ter a mesma demanda**, e isso é correto: sem o coletor, a
parte da área que só ele alcançava não é atendida por ninguém.

**Vazão, receita e população NÃO são somadas** — e isso é regra, não pendência. Elas são
**dado da sub-bacia**, e o motor não inventa o valor delas para o cenário sem coletor: se
desligar a CTS muda a vazão da sub-bacia, **quem atualiza a base é quem cadastra**. A escolha
de considerar ou não a CTS não mexe em receita.

Duas consequências que valem estar escritas:

- **A ETE é dimensionada com a vazão que estiver na base.** Se ela não refletir o cenário sem
  coletor, falta o esgoto que vinha por ele. O motor avisa em toda rodada que absorve uma CTS.
- **A receita da linha da CTS não é herdada.** Sem o coletor, as ligações que ele atenderia
  são ligadas pelas obras da sub-bacia e cobradas pelo **ticket dela**. O ticket em si não
  muda com a escolha: ele sai da base comercial da própria sub-bacia (`receita ÷ ligações
  atuais exclusivas`), e o número consolidado não entra nessa divisão.

Enquanto a exportação não trouxer os valores apurados, as oito são derivadas
(`exclusiva + CTS pareada`, que reproduz exatamente a soma antiga) por
`dev/preencher_sobreposicao_cts.py`, e a planilha marca isso em `sobreposicao_origem`
(`derivado_soma` | `sem_cts`). Coluna ausente faz o motor voltar a somar, avisando.

**População não tem versão residencial**: indústria não mora, então `universo_populacao` já é
residencial. Cidade que mede a meta em população ignora as quatro colunas acima.

**O QUE A EXPORTAÇÃO PRECISA PRODUZIR.** As quatro colunas são **medida**, não derivação: a
apuração de quantas ligações e economias são residenciais é da base comercial, e é ela que
deve preenchê-las. Enquanto a exportação não as trouxer, elas são derivadas
(`total − parcela industrial`, economias pela proporção das ligações) por
`dev/preencher_recorte_residencial.py` no repositório do backend, e a planilha marca isso na
coluna de controle `residencial_origem` (`derivado` | `sem_industria`). **Valor derivado não
é medição** — quem usa o resultado de uma rodada só-residencial precisa saber qual dos dois
está lendo.

Coluna ausente ou vazia **não é tratada como zero**: o motor avisa em voz alta
(`[ALERTA] COBERTURA SO RESIDENCIAL pedida, mas o banco nao tem ...`) e mede a cobertura no
total. Cair no total em silêncio seria pior — a rodada responderia "só residencial" medindo
todo mundo.

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
    "COBERTURA_SO_RESIDENCIAL": false,
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
| `COBERTURA_SO_RESIDENCIAL` | bool | `false` | `false` = meta medida nos totais; `true` = medida nas colunas `*_residencial`. Não afeta receita, VPL nem vazão |
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

**Reprocessar o mesmo `run_id` é seguro — enquanto a rodada não tiver publicado.** A
publicação apaga a rodada anterior (`DELETE FROM otim_meta WHERE run_id = ...`) e o
`ON DELETE CASCADE` remove todos os detalhes, antes de regravar. Vale também para o
diagnóstico, para o status (upsert) e para a cópia congelada em blob, que substitui a
partição `run_id=<rid>` em vez de acrescentar a ela.

### A gramática do `run_id`

`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` — alfanumérico, `.`, `_` e `-`, começando por
alfanumérico, até 128 caracteres. O job **recusa** (`ValueError`) qualquer coisa fora
disso antes de gravar; a coluna no DDL é `text` sem `CHECK`, então quem cunha o id é
quem garante a forma.

Não é preciosismo de estilo: o `run_id` vira **caminho de partição** no blob e
**literal SQL** na substituição da rodada em Delta. Uma aspa simples fecharia o
literal (`r1' OR run_id <> 'r1` casaria com todas as rodadas, e o overwrite levaria a
tabela inteira); `/` e `..` desviariam o diretório apagado; e qualquer caractere que o
Spark escapa ao gravar partição (`/`, `=`, `%`, espaço) faria a pasta real ter outro
nome, com o efeito silencioso de a substituição virar no-op e a duplicação voltar.

`persistencia.novo_run_id()` já gera dentro desta gramática
(`run_<AAAAMMDD>_<HHMMSS>_<hex6>`); se o backend cunhar o id por conta própria — que é
o caso — vale espelhar a regra na borda dele.

### A regra do `run_id`

**Um `run_id` congela na primeira publicação bem-sucedida.**

| `controle.run_status` | reexecutar com o mesmo `run_id` |
|---|---|
| `PENDENTE`, `RODANDO`, `ERRO`, `FALHOU_QUALIDADE`, `CANCELADA` | **pode** — é o retry técnico |
| `SUCESSO` | **não** — gere um `run_id` novo, e recuse o pedido (`409`) |

A condição é o `run_status`, e não a intenção de quem dispara, porque a publicação é
atômica: `public.otim_*` e `run_status = SUCESSO` entram na mesma transação. Então "já
foi publicado" é fato consultável, não julgamento.

O motivo não é o cache do front (isso um refresh resolve) — é a **auditoria**. O job lê o
cadastro no instante da execução, então os mesmos parâmetros, depois de uma correção no
cadastro, produzem outro plano; republicando sob o mesmo id, o `DELETE`+`INSERT` apaga o
resultado que alguém aprovou em reunião, e o `blob_uri` daquela rodada passa a apontar
para outra coisa. Rodada nova é rodada nova.

Para a reexecução não virar uma rodada solta no histórico, guarde o id de origem na
`run_request` (campo `reprocessa_de`, nulo na primeira rodada). É o que permite ao front
rotular "reprocessamento de `run_...`" e comparar antes/depois.

Demais consequências para o backend:

- **Retry é livre** dentro da regra acima. Job que morreu no meio, cluster reiniciado,
  timeout de rede: dispare o mesmo `run_id` de novo.
- **Um `run_id` = uma rodada.** `otim_meta` nunca tem duas linhas com o mesmo `run_id`.
- **Publicar e marcar `SUCESSO` é uma transação só.** Não existe o estado "dados publicados
  mas status `RODANDO`". Se o commit falhar, nada foi publicado e o status não avançou.

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
