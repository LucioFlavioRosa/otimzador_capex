# Revisão do pacote `Otimizador_Producao` — achados + diffs sugeridos

Revisão de código do pacote de produção (Databricks + Azure Postgres), no escopo do
"Checklist de revisão" do `Plano_Producao_Databricks.md` §6 e das prioridades pedidas
(transação, SQL/segredos, DDL, portão de qualidade, orquestração, Fase 2b).

Alvo: Python 3.10+, Databricks Runtime.

> **Estado: TUDO APLICADO** (D1–D9), na ordem sugerida. Suíte: **61 passed, 13 skipped**
> (baseline era 30 passed, 1 failed). Dois achados novos — **C6** e **A6** — só apareceram
> ao *executar* o portão, e estão marcados como tal. O achado **C4** foi corrigido no
> diagnóstico depois do teste: a consequência não era publicação silenciosa, e sim um
> `OverflowError` opaco dentro do CP-SAT. Ver §6 para o resumo do que mudou em cada arquivo.

---

## 0. Estado da suíte (baseline, antes de qualquer mudança)

```
$ pip install -r requirements-prod.txt      # local: pandas 3.0.3, ortools 9.15, openpyxl 3.1.5
$ pytest -q tests/
..........................F....                                          [100%]
FAILED tests/test_nucleo.py::test_separabilidade_por_cidade_e_exata - ModuleNotFoundError
1 failed, 30 passed in 2.16s
```

Dois desvios em relação à documentação:

- São **31 testes**, não 26 (`README_producao.md` e o plano falam em 26).
- A suíte **não está verde no pacote entregue**: `test_separabilidade_por_cidade_e_exata`
  faz `import testes_otimizador`, e esse módulo (suíte legada do repo de desenvolvimento)
  **não foi empacotado**. Todo o resto do pacote é autossuficiente — só este teste não é.

O invariante "a suíte tem de continuar verde" **já está violado na entrega**. O diff D9
resolve sem tocar em semântica nem em valores golden (converte um erro de dependência
ausente em `skip`, exatamente como `require_bank` e `solver_or_skip` já fazem no pacote).

---

## 1. Achados por severidade

### CRÍTICO

#### C1 — O `run_id` da rodada é descartado; nada correlaciona `controle` com `public`
`job_databricks.py:91`

```python
tabs = P.materializar(cen, res, banco=f"postgres://{schema_input}", params=p)
```

`persistencia.materializar` tem assinatura `(cen, res, banco=None, params=None, run_id=None, ...)`
e faz `rid = run_id or novo_run_id()` (`persistencia.py:103`). Como o job **não passa
`run_id`**, cada rodada gera um id novo (`run_20260727_1412_a1b2c3`).

Consequências:

1. `public.otim_meta.run_id` ≠ `controle.run_status.run_id` ≠ `controle.run_diagnostico.run_id`.
   O backend não consegue ligar a requisição ao resultado — o contrato inteiro entre os
   dois schemas fica quebrado.
2. **A idempotência é ficcional.** `publicar_postgres` apaga por
   `DELETE FROM otim_meta WHERE run_id = <run_id gerado agora>`, que nunca casa com a
   rodada anterior. Reprocessar o mesmo `run_id` **acumula** um conjunto completo de
   resultados a cada tentativa, em vez de substituir. O `README_producao.md` afirma o
   contrário ("Reprocessar: rode o mesmo `run_id` de novo — tudo é idempotente").

Responde o item do checklist "Retry/idempotência: reprocessar `run_id` é seguro em todos
os caminhos?" — **não é**. Correção: D1 (uma linha).

#### C2 — `foco_cobertura` default 1.0 no job inverte a função objetivo
`job_databricks.py:52` × `otimizador_capex_v62.py:1417-1422`

```python
foco_cobertura=p.get("FOCO_COBERTURA", 1.0),      # job
def ler_banco(..., foco_cobertura=None, ...)       # motor
```

No motor:

```python
if foco_cobertura is not None:
    _a = min(1.0, max(0.0, float(foco_cobertura)))
    cen.peso_cobertura = min(_Lam0*_a/(1.0-_a+1e-9), _cap); cen.foco_cobertura = _a
else:
    cen.peso_cobertura = float(peso_cobertura); cen.foco_cobertura = None
```

Com `foco_cobertura = 1.0` → `_a = 1.0` → `_Lam0*1.0/1e-9` → satura em `_cap`, o **peso
máximo de cobertura**: o objetivo passa a ser essencialmente "só cobertura, VPL
irrelevante". O default do motor (`None` → `peso_cobertura = 0.0`) é o extremo oposto:
"só VPL".

Ou seja: **um `run_request` que omita `FOCO_COBERTURA` roda o otimizador no extremo oposto
do caminho Excel/notebook**, e o rótulo da rodada ainda registra `foco 1.0`
(`job_databricks.py:105`) — um parâmetro que ninguém pediu. O plano publicado é outro.

#### C3 — `ete_faseada` default `True` no job muda o conjunto de obras do modelo
`job_databricks.py:49` × `otimizador_capex_v62.py:1231`

```python
ete_faseada=p.get("ETE_FASEADA", True),           # job
def ler_banco(..., ete_faseada=False, ...)         # motor
```

Em `ler_banco`: `if ete_faseada:  # cada ETE -> K modulos-OBRA (obras reais, priorizadas)`.
Não é um ajuste fino: muda a **cardinalidade do problema** (cada ETE vira K obras) e o
custo em `avaliar` (`v62:158`). Mesma classe de C2 — divergência silenciosa entre
produção e o caminho de desenvolvimento, sem nenhum aviso no log.

C2 e C3 têm a mesma raiz: `_params_para_ler_banco` **reinventa defaults** em vez de
delegar ao motor. Correção: D2.

#### C6 — O portão de qualidade reprovava 100% das rodadas bem-sucedidas
`qualidade.py:51` (encontrado **executando** o portão, não na leitura)

```python
st = str(res.get("milp_status", "")).upper()
add("Status do solver", st in ("OPTIMAL", "FEASIBLE"), ...)
```

O `cpsat63` nunca devolve `OPTIMAL`/`FEASIBLE`. Ele monta a string em pt-BR, com sufixos
(`cpsat63:241, 618, 622`):

```python
res["milp_status"] = ("OTIMO" if st==cp_model.OPTIMAL else "VIAVEL(limite de tempo)") + _ob_tag + ...
```

Rodando o solver sobre o banco de teste CTS, o valor real é `"OTIMO | OBRIG 0/0"`. A
comparação `in ("OPTIMAL","FEASIBLE")` é **sempre falsa**: o portão reprova toda rodada,
o job marca `FALHOU_QUALIDADE` e **nada jamais seria publicado**. Os valores possíveis são
`OTIMO…`, `VIAVEL(limite de tempo)…` e `SEM SOLUCAO(<st>)`.

Corrigido para `st.startswith(("OTIMO","VIAVEL","OPTIMAL","FEASIBLE"))`, com teste
parametrizado sobre as quatro formas reais + a de fracasso.

#### C4 — Sem orçamento a rodada morre com `OverflowError` dentro do CP-SAT
`otimizador_capex_v62.py:1130-1142`, `carregar_postgres.py:25-42`, `ddl_input.sql`

O motor resolve o teto de CAPEX nesta ordem: parâmetro `orcamento` → aba `orcamento`
(`orc_reg`, lido em `v62:973`) → **`INF`**:

```python
elif u in orc_reg:               orc[un] = orc_reg[u]
elif r in orc_reg:               orc[un] = orc_reg[r]
else:                            orc[un] = INF
if orcamento is None and not orc_reg:
    print("  [aviso] orcamento nao informado (parametro nem aba) -> CAPEX sem teto")
```

No caminho Databricks as duas primeiras fontes podem falhar juntas:

- `ABAS_INPUT` **não tem a aba `orcamento`**, e `ddl_input.sql` **não tem
  `input.orcamento`** — a segunda fonte não existe em produção;
- se o backend omitir (ou escrever com o nome errado — ver M4) `ORCAMENTO`, a primeira
  também não existe.

Resultado: `orc[un] = INF`, e o único sinal antes disso é um `print` no log do driver.

> **Correção do diagnóstico (depois de executar).** A primeira versão deste relatório dizia
> que a rodada seria *publicada como SUCESSO* com plano irrestrito. Executando: o CP-SAT
> estoura antes disso, em `cpsat63.py:463`, com
> `OverflowError: cannot convert float infinity to integer`. Ou seja, a falha é **ruidosa**,
> não silenciosa — mas chega ao operador como um `OverflowError` no meio do solver, sem
> nenhuma pista de que o problema é a falta de `ORCAMENTO` no `run_request`. A observação
> sobre o portão continua válida: a checagem "teto anual respeitado" compara gasto contra
> `INF` e passa trivialmente, então ela não protegeria nada num caminho que tolerasse `INF`
> (o `avaliar`/build-all, por exemplo, ignora o teto).

Correção: D3 (guarda no job, com mensagem que diz o que falta) + D6 (checagem "teto
definido" no portão, como defesa em profundidade) + D8 (tabela `input.orcamento` no DDL).

#### C5 — `snapshot_input_para_xlsx` transforma qualquer falha de leitura em "tabela opcional ausente"
`carregar_postgres.py:60-64`

```python
for aba, tabela in ABAS_INPUT.items():
    try:
        df = pd.read_sql(f'SELECT * FROM "{schema}"."{tabela}"', eng)
    except Exception:
        continue                      # tabela ausente/opcional
```

O `except Exception` engole **tudo**: permissão negada, timeout, queda de conexão, erro de
tipo. Uma falha transitória ao ler `metas_cobertura` produz um Cenário **sem metas de
cobertura** — que resolve normalmente, passa no portão (as checagens de meta são todas
condicionais a `rmc is not None`) e é publicado como SUCESSO.

Só `subbacia-operacional` tem verificação explícita (`carregar_postgres.py:86`). As outras
14 podem sumir em silêncio. Correção: D4 — distinguir SQLSTATE `42P01` (tabela inexistente)
em abas declaradamente opcionais de qualquer outro erro, que deve estourar.

---

### ALTO

#### A1 — O portão de qualidade passa com tabelas ausentes ou vazias
`qualidade.py:55-109`

**Todas** as checagens são condicionais à existência da tabela (`if rs is not None:`,
`if ra is not None and rmeta is not None:`, …). Se `tabs` vier degradado, o portão não
reprova — ele simplesmente **roda menos checagens** e imprime
`QUALIDADE OK — 2 checagens criticas passaram`.

Faltam três checagens que o portão precisa ter antes de liberar publicação:

1. **presença e não-vazio** das tabelas obrigatórias;
2. **`run_id` único e idêntico** em todas as tabelas (hoje um `run_id` divergente só
   apareceria como violação de FK **dentro** da transação de publicação, virando `ERRO`
   técnico em vez de `FALHOU_QUALIDADE` explicado);
3. **duplicatas nas PKs** de `CHAVES` — mesma lógica: é melhor barrar no portão do que
   abortar o `INSERT` com um erro de constraint difícil de ler.

Correção: D6.

#### A2 — `publicar_postgres` fecha a conexão do chamador; o SUCESSO fica fora da transação
`publicacao.py:194-257`, `job_databricks.py:102-108`

`_conectar` aceita uma conexão já aberta (`if hasattr(pg, "cursor")`), mas
`publicar_postgres` faz `conn.close()` no `finally` **sempre** — inclusive na conexão que o
chamador passou. Na prática isso **impede compor** publicação e status na mesma transação.

Efeito no job:

```python
payload = PUB.publicar(tabs, pg=pg_url, ...)          # transação A: commit
PUB.marcar_status_controle(pg_url, run_id, "SUCESSO") # transação B: outra conexão
```

Uma queda entre as duas deixa a rodada **publicada em `public.otim_*` mas com
`controle.run_status = RODANDO` para sempre**. Com C1 corrigido o retry é seguro (apaga e
regrava), então a janela é recuperável — mas o plano pede "tudo-ou-nada por `run_id`", e
hoje o estado observável e o dado publicado podem divergir. Correção: D5 (+ D5b no job).

#### A3 — O fallback SQLAlchemy de `_conectar` descarta a escrita sem erro
`publicacao.py:194-207`

```python
try:
    import psycopg2
    return psycopg2.connect(pg), "psycopg2"
except ImportError:
    pass
try:
    from sqlalchemy import create_engine
    return create_engine(pg).raw_connection(), "psycopg2"
```

Verificado nesta máquina (SQLAlchemy 2.0.51):

```
sqlalchemy.pool.base._ConnectionFairy.__exit__:
    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
        return None
```

O `with conn:` usado em todos os escritores **não faz commit** nesse caminho: devolve a
conexão ao pool, que faz rollback. `marcar_status` / `marcar_status_controle` (os dois que
não importam `psycopg2.extras`) gravariam **nada**, sem exceção nenhuma — o status some em
silêncio.

Dois problemas laterais no mesmo bloco:

- só `ImportError` é capturado. Se `pg_url` vier como `postgresql+psycopg2://…` (formato
  que `carregar_postgres`/SQLAlchemy aceitam), `psycopg2.connect` levanta
  `OperationalError`, que **não** é capturada — o fallback nunca roda. O mesmo secret
  `pg_url` precisa servir aos dois consumidores; hoje só `postgresql://…` funciona nos
  dois, e isso não está documentado.
- `publicar_postgres` e `gravar_diagnostico` já fazem `from psycopg2.extras import
  execute_values` na primeira linha — sem psycopg2 eles estouram antes de chegar em
  `_conectar`. O fallback é morto para eles e quebrado para os outros.

Correção: D5 — psycopg2 vira dependência dura (já está em `requirements-prod.txt`), o
fallback sai, e o commit/rollback passa a ser explícito.

#### A4 — Suíte vermelha na entrega
Ver §0. Correção: D9.

#### A6 — `matplotlib` faltando em `requirements-prod.txt`
(encontrado **executando**, não na leitura)

`job_databricks.rodar` faz `import dashboard_otimizador_v2 as D` e `P.set_engine(M, D)`;
`dashboard_otimizador_v2.py:20` importa `matplotlib` no topo do módulo. `matplotlib` não
está em `requirements-prod.txt`. No Databricks Runtime ele vem pré-instalado, então o job
funciona **por sorte**; num venv limpo — que é exatamente o que a Fase 5 (wheel + CI) vai
construir — o job quebra no import. Corrigido no `requirements-prod.txt`.

#### A5 — `ddl_input.sql` não tem uma única PK/UNIQUE; duplicata de linha corrompe o resultado
`ddl_input.sql:10-186`

Nenhuma das 15 tabelas de `input` tem PK, UNIQUE, NOT NULL ou FK. O front escreve nesse
schema. Duas linhas duplicadas produzem resultados diferentes conforme a tabela, e nenhum
deles é um erro visível:

- `subop = {d["sub_bacia"]: d for d in L("subbacia-operacional", …)}` (`v62:978`) —
  **a última linha vence**, a primeira desaparece;
- `comp[d.get("sub_bacia")].append(d)` (`v62:1144`) — **a obra é duplicada**, e o CAPEX
  dela entra duas vezes no plano.

O segundo caso é silencioso de ponta a ponta: passa no portão (as reconciliações fecham,
porque tudo é consistente com um banco que de fato tem duas obras) e publica. A defesa
tem que estar no banco. Correção: D8.

---

### MÉDIO

- **M1 — Tipos do DDL.** `universo_populacao`, `populacao_atual`, `populacao_novas_obras`
  são `text` em `subbacia_operacional` e `cts_operacional` (são números);
  `potencial_crescimento` é `integer` em `subbacia_operacional` e `double precision` em
  `cts_operacional` (mesmo campo, tipos diferentes — e o motor trata como fator contínuo);
  `cobertura_pct` é `integer` em `metas_cobertura` e `fator_esgoto`. Nos casos `integer`,
  o Postgres **arredonda em silêncio**: uma meta de 90,5% vira 90 e um crescimento de 1,5
  vira 2. Correção: D8.

- **M2 — Colunas-lixo no DDL.** `input.cidade_operacional` tem `"Unnamed: 3"` e uma coluna
  cujo *nome* é uma frase de documentação de 190 caracteres; `input.fator_esgoto` tem
  `"Unnamed: 4"` e outra igual. São células de comentário do Excel de amostra que vazaram
  para o esquema físico. Não quebram o motor (colunas extras são ignoradas na leitura), mas
  o front tem de conviver com elas e qualquer `SELECT *` as carrega. Correção: D8.

- **M3 — `_ler_run_request` depende do paramstyle do driver.** `job_databricks.py:33`
  passa `%(rid)s` para `pd.read_sql` sobre um **Engine SQLAlchemy**. Verificado: pandas
  embrulha a string em `text()`, cuja sintaxe de bind é `:rid`; o `%(rid)s` chega literal
  ao driver. Com psycopg2 (paramstyle `pyformat`) isso **funciona por acidente** — o driver
  faz a substituição. Com qualquer outro driver quebra (confirmado em sqlite:
  `OperationalError: near "%"`). A forma portável é `text(...)` + `:rid`. Correção: D7.

- **M4 — Chave errada no `run_request` é ignorada em silêncio.** `_params_para_ler_banco`
  usa `p.get("ORCAMENTO")` etc.; se o backend escrever `orcamento` (minúsculo) ou
  `ORÇAMENTO`, o valor é descartado sem aviso — e cai no C4. Correção: D2 (rejeita chaves
  desconhecidas).

- **M5 — `PESO_COBERTURA` do `run_request` nunca chega ao motor.** Está na assinatura de
  `ler_banco` (`peso_cobertura=0.0`) e não é traduzido em `_params_para_ler_banco`.
  Correção: D2.

- **M6 — `MAX_TIME_S` e `WORKERS` não vêm do `run_request`.** Fixos em 300 s / 8 workers na
  assinatura de `rodar`. Sem controle por rodada; num cluster maior ou num cenário maior,
  o operador não tem como ajustar sem alterar o job.

- **M7 — O `except` do `rodar` pode mascarar a exceção original.**
  `job_databricks.py:111-114`: se `marcar_status_controle` falhar (banco fora do ar — a
  causa mais provável da falha original), a exceção dela substitui a original e o
  `traceback` útil se perde. Correção: D7.

- **M8 — DDL de resultado executado a cada rodada, dentro da transação de publicação.**
  `publicar_postgres(..., criar=True)` roda `ddl_postgres(tabs)` inteiro — incluindo
  `CREATE OR REPLACE VIEW` — em toda publicação. Dois efeitos: `CREATE TABLE IF NOT EXISTS`
  **não corrige drift** (uma coluna nova no `tabs` não é adicionada; o `INSERT` é que falha,
  com erro obscuro), e o `CREATE OR REPLACE VIEW` toma lock, o que pode bloquear leituras do
  front concorrentes. Em produção isso deveria ser migration, com `criar_schema=False` no
  job.

- **M9 — `create_engine` por chamada, sem `dispose()`.** `carregar_postgres.py:45-47`,
  `job_databricks.py:32`. Cada chamada cria um pool novo que nunca é fechado. Num job de
  vida curta é tolerável; num cluster interativo/`all-purpose` vaza conexões.

- **M10 — `input` sem discriminador de unidade.** `snapshot_input_para_xlsx` faz
  `SELECT *` sem filtro, e o recorte por unidade acontece depois, em memória, no
  `ler_banco`. Com o cadastro nacional crescendo, toda rodada de toda unidade lê o banco
  inteiro. Um `WHERE` por unidade (ou views por unidade em `input`) resolve — mas exige
  decidir a chave de particionamento, então é decisão de arquitetura, não correção pontual.

---

### BAIXO

- **B1 — Identificadores por f-string.** Schema, tabela e lista de colunas são interpolados
  em `publicacao.py:224/229/245`, `carregar_postgres.py:62` e `job_databricks.py:33`. **Os
  valores** estão todos parametrizados (`%s`, `%(rid)s`) — não há injeção por dado de
  usuário. Os identificadores vêm de constantes do módulo e de `df.columns`, não de input
  externo. Ainda assim, `psycopg2.sql.Identifier` é a forma correta e custa pouco; hoje uma
  coluna com maiúscula ou caractere especial quebraria o `INSERT`.
- **B2 — `pd.isna(v) if not isinstance(v, (list, dict)) else False`** (`publicacao.py:236`):
  a expressão condicional aninhada é difícil de ler e levanta se `v` for `ndarray`.
  `if not isinstance(v, (list, dict)) and pd.isna(v):` diz o mesmo.
- **B3 — Truncamento de aba sem verificação.** `aba[:31]` (`carregar_postgres.py:69`) —
  hoje o maior nome tem 27 caracteres, mas se um dia passar de 31 a aba é truncada e o
  motor, que lê por nome exato, simplesmente não a encontra. Um `assert` no import resolve.
- **B4 — "Plano não-vazio" é `aviso`.** Decisão de negócio: publicar uma rodada com zero
  obras construídas é resultado legítimo ou falha? Se for falha, muda para `critico`.
- **B5 — Tolerância absoluta de R$ 0,01.** Adequada na escala atual (o erro de ponto
  flutuante em somas de ~R$ 10⁹ fica na casa de 10⁻⁴). Se o horizonte crescer uma ordem de
  grandeza, vale trocar por `max(0.01, 1e-9 * abs(referencia))`.
- **B6 — Docs desatualizadas.** `README_producao.md`: "26 testes" (são 31) e "Reprocessar:
  tudo é idempotente" (falso enquanto C1 não for corrigido). O `pg_url` precisa dizer
  explicitamente o formato aceito (ver A3).
- **B7 — Nenhuma credencial no código.** Varredura por `password|senha|SharedAccessKey|
  token=|api_key|postgres://` só encontrou o placeholder de docstring
  `pg='postgresql://user:senha@host:5432/otimizador'` (`publicacao.py:21`) e os
  `dbutils.secrets.get(...)` esperados. **Item do checklist OK.**

---

## 2. Diffs sugeridos

### D1 — passar o `run_id` da rodada para a materialização  *(corrige C1)*

```diff
--- a/job_databricks.py
+++ b/job_databricks.py
@@
-        # 5) materializacao
-        tabs = P.materializar(cen, res, banco=f"postgres://{schema_input}", params=p)
+        # 5) materializacao — o run_id da rodada MANDA: e ele que liga controle.* a public.otim_*
+        #    e e a chave do DELETE idempotente em publicar_postgres. Sem isso, cada retry
+        #    publica um conjunto novo em vez de substituir o anterior.
+        tabs = P.materializar(cen, res, run_id=run_id,
+                              banco=f"postgres://{schema_input}", params=p)
```

### D2 — traduzir `run_request.params` sem inventar defaults  *(corrige C2, C3, M4, M5)*

```diff
--- a/job_databricks.py
+++ b/job_databricks.py
@@
-def _params_para_ler_banco(p):
-    """Traduz o payload da run_request para os kwargs do ler_banco/carregar_postgres."""
-    return dict(
-        orcamento=p.get("ORCAMENTO"),
-        orcamento_total=p.get("ORCAMENTO_TOTAL"),
-        horizonte_capex=p.get("HORIZONTE_CAPEX"),
-        ete_faseada=p.get("ETE_FASEADA", True),
-        ete_fixo=p.get("ETE_FIXO", False),
-        metas_cobertura=p.get("METAS_COBERTURA"),
-        foco_cobertura=p.get("FOCO_COBERTURA", 1.0),
-        penalidade_cobertura=p.get("PENALIDADE_COBERTURA", "meta+cobertura"),
-        peso_cidade=p.get("PESO_CIDADE", {}),
-        data_inicio=p.get("DATA_INICIO"),
-        regional=p.get("REGIONAL"),
-        unidade=p.get("UNIDADE"),
-        curva_adocao=p.get("CURVA_ADOCAO", "scurve"),
-        base_receita=p.get("BASE_RECEITA", "arrecadada"),
-        anos_extra_conclusao=p.get("ANOS_EXTRA_CONCLUSAO", 3),
-        usar_cts=p.get("USAR_CTS", True),
-        incluir_industrial=p.get("INCLUIR_INDUSTRIAL", True),
-    )
+# chave do run_request (JSONB) -> kwarg do ler_banco. Chaves de controle do job ficam fora.
+MAPA_PARAMS = {
+    "ORCAMENTO": "orcamento",                   "ORCAMENTO_TOTAL": "orcamento_total",
+    "HORIZONTE_CAPEX": "horizonte_capex",       "ETE_FASEADA": "ete_faseada",
+    "ETE_FIXO": "ete_fixo",                     "METAS_COBERTURA": "metas_cobertura",
+    "PESO_COBERTURA": "peso_cobertura",         "FOCO_COBERTURA": "foco_cobertura",
+    "PENALIDADE_COBERTURA": "penalidade_cobertura", "PESO_CIDADE": "peso_cidade",
+    "DATA_INICIO": "data_inicio",               "REGIONAL": "regional",
+    "UNIDADE": "unidade",                       "CURVA_ADOCAO": "curva_adocao",
+    "BASE_RECEITA": "base_receita",             "ANOS_EXTRA_CONCLUSAO": "anos_extra_conclusao",
+    "USAR_CTS": "usar_cts",                     "INCLUIR_INDUSTRIAL": "incluir_industrial",
+}
+CHAVES_DO_JOB = {"USUARIO", "MAX_TIME_S", "WORKERS"}
+
+
+def _params_para_ler_banco(p):
+    """Traduz o payload da run_request para os kwargs do ler_banco/carregar_postgres.
+
+    REGRA: chave ausente NAO vira default do job — nao e repassada, e o `ler_banco` aplica
+    o default dele. E o que garante que o job e o caminho Excel resolvam o MESMO problema.
+    (Os defaults antigos do job divergiam do motor: ete_faseada=True vs False e
+    foco_cobertura=1.0 vs None, este ultimo saturando o peso de cobertura -> objetivo
+    "so cobertura" em vez de "so VPL".)
+
+    Chave desconhecida e ERRO: um `orcamento` minusculo passaria batido e a rodada sairia
+    sem teto de CAPEX.
+    """
+    desconhecidas = sorted(set(p) - set(MAPA_PARAMS) - CHAVES_DO_JOB)
+    if desconhecidas:
+        raise ValueError(f"run_request.params com chaves desconhecidas: {desconhecidas}")
+    return {kw: p[chave] for chave, kw in MAPA_PARAMS.items() if chave in p}
```

### D3 — não rodar sem teto de CAPEX  *(corrige C4, parte 1/3)*

```diff
--- a/job_databricks.py
+++ b/job_databricks.py
@@
         p = _ler_run_request(pg_url, run_id, schema=schema_ctrl)
         kw = _params_para_ler_banco(p)
+        # Sem ORCAMENTO/ORCAMENTO_TOTAL (e sem a aba `orcamento` no input), o motor cai em
+        # orc[unidade] = INF e devolve um plano SEM TETO — que passa no portao e e publicado.
+        # Falha aqui, antes de gastar o solver.
+        if kw.get("orcamento") is None and kw.get("orcamento_total") is None:
+            raise ValueError("run_request sem ORCAMENTO nem ORCAMENTO_TOTAL: "
+                             "o plano sairia sem teto de CAPEX")
```

### D4 — falha de leitura ≠ tabela opcional ausente  *(corrige C5)*

```diff
--- a/carregar_postgres.py
+++ b/carregar_postgres.py
@@
+# abas que podem legitimamente nao existir (a unidade nao tem CTS; orcamento veio por parametro)
+ABAS_OPCIONAIS = {"subbacia-cts", "cts-operacional", "componentes-cts-capex",
+                  "orcamento", "sistema-operacional"}
+
+
+def _e_tabela_ausente(e):
+    """True so para 'relation does not exist' (SQLSTATE 42P01). Qualquer outro erro
+    (permissao, timeout, conexao) NAO pode ser confundido com aba opcional."""
+    return getattr(getattr(e, "orig", e), "pgcode", None) == "42P01" or "42P01" in str(e)
+
+
 def snapshot_input_para_xlsx(pg_url, destino_xlsx, schema="input"):
@@
         for aba, tabela in ABAS_INPUT.items():
             try:
                 df = pd.read_sql(f'SELECT * FROM "{schema}"."{tabela}"', eng)
-            except Exception:
-                continue                      # tabela ausente/opcional
+            except Exception as e:
+                if aba in ABAS_OPCIONAIS and _e_tabela_ausente(e):
+                    continue                  # a unidade realmente nao tem essa aba
+                raise RuntimeError(
+                    f"falha ao ler {schema}.{tabela} (aba '{aba}'): {e}") from e
```

E, no mesmo arquivo, cobrir a aba `orcamento` (parte 2/3 de C4):

```diff
     "subbacia-cts":                "subbacia_cts",
     "cts-operacional":             "cts_operacional",
     "componentes-cts-capex":       "componentes_cts_capex",
+    # teto de CAPEX por regional/unidade (fallback quando ORCAMENTO nao vem no run_request)
+    "orcamento":                   "orcamento",
 }
```

### D5 — transação explícita e composição de escritas  *(corrige A2, A3)*

```diff
--- a/publicacao.py
+++ b/publicacao.py
@@
+import contextlib as _contextlib
+
 # ------------------------------------------------------------------ POSTGRES
 def _conectar(pg):
-    """Devolve (conn, driver). Aceita string de conexao ou conexao ja aberta."""
-    if hasattr(pg, "cursor"):
-        return pg, "psycopg2"
-    try:
-        import psycopg2
-        return psycopg2.connect(pg), "psycopg2"
-    except ImportError:
-        pass
-    try:
-        from sqlalchemy import create_engine
-        return create_engine(pg).raw_connection(), "psycopg2"
-    except Exception as e:
-        raise RuntimeError("instale psycopg2-binary ou sqlalchemy para publicar no Postgres") from e
+    """Devolve (conn, proprio). `proprio` = True quando fomos nos que abrimos a conexao.
+
+    Aceita uma conexao ja aberta — e assim que o job junta publicacao e status numa
+    transacao so. psycopg2 e dependencia dura (execute_values ja exige): o antigo fallback
+    para `create_engine(pg).raw_connection()` era pior que inutil, porque o `with conn:`
+    de uma PoolProxiedConnection chama close() em vez de commit — a escrita sumia sem erro.
+    """
+    if hasattr(pg, "cursor"):
+        return pg, False
+    import psycopg2                      # requirements-prod.txt: psycopg2-binary
+    return psycopg2.connect(pg), True    # DSN aceito: postgresql://user:senha@host:5432/db
+
+
+@_contextlib.contextmanager
+def _transacao(pg):
+    """Um cursor numa transacao. Se abrimos a conexao, commitamos/rollbackamos e fechamos;
+    se a conexao veio de fora, quem manda no commit e o chamador (permite compor)."""
+    conn, proprio = _conectar(pg)
+    try:
+        with conn.cursor() as cur:
+            yield cur
+        if proprio:
+            conn.commit()
+    except Exception:
+        if proprio:
+            conn.rollback()
+        raise
+    finally:
+        if proprio:
+            try:
+                conn.close()
+            except Exception:
+                pass
```

Os quatro escritores passam a usá-lo (mesma forma nos quatro; mostro `publicar_postgres`
e `marcar_status_controle`):

```diff
 def publicar_postgres(tabs, pg, schema="public", criar=True, substituir=True, verbose=True):
     from psycopg2.extras import execute_values
-    conn, _ = _conectar(pg)
     rid = tabs["run_meta"]["run_id"].iloc[0]
     escritos = []
-    try:
-        with conn:
-            with conn.cursor() as cur:
-                if criar:
-                    ...
-    finally:
-        try:
-            conn.close()
-        except Exception:
-            pass
+    with _transacao(pg) as cur:
+        if criar:
+            ...                      # corpo inalterado, um nivel de indentacao a menos
```

```diff
 def marcar_status_controle(pg, run_id, status, erro=None, schema="controle"):
-    conn, _ = _conectar(pg)
-    try:
-        with conn:
-            with conn.cursor() as cur:
-                cur.execute(...)
-    finally:
-        try:
-            conn.close()
-        except Exception:
-            pass
+    with _transacao(pg) as cur:
+        cur.execute(...)
     return status
```

### D5b — publicar dados e SUCESSO na mesma transação  *(fecha A2)*

```diff
--- a/job_databricks.py
+++ b/job_databricks.py
@@
-        # 7) publicacao transacional + notificacao
-        payload = PUB.publicar(
-            tabs, pg=pg_url, blob=None,
-            notificar=({"service_bus": service_bus, "fila": "otimizacoes"} if service_bus else None),
-            rotulo=f"Unidade {kw.get('unidade')} · foco {kw.get('foco_cobertura')}",
-            usuario=p.get("USUARIO", "job-databricks"),
-        )
-        PUB.marcar_status_controle(pg_url, run_id, "SUCESSO")
+        # 7) publicacao transacional: as run_* e o SUCESSO entram no MESMO commit, para o
+        #    estado observavel nunca divergir do dado publicado. A notificacao vem DEPOIS.
+        import psycopg2
+        conn = psycopg2.connect(pg_url)
+        try:
+            with conn:
+                PUB.publicar_postgres(tabs, conn, criar=False, verbose=True)
+                PUB.marcar_status_controle(conn, run_id, "SUCESSO", schema=schema_ctrl)
+        finally:
+            conn.close()
+        payload = PUB._payload(tabs, blob_uri=None, extra={"usuario": p.get("USUARIO")})
+        if service_bus:
+            PUB.notificar_service_bus(service_bus, "otimizacoes", payload)
```

`criar=False` fecha M8 — a DDL de resultado passa a ser migration, rodada uma vez pelo DBA
com `publicacao.ddl_postgres(tabs)` (o próprio `README_producao.md` já descreve isso no
passo 2 do setup).

### D6 — checagens que faltam no portão  *(corrige A1 e fecha C4, parte 3/3)*

```diff
--- a/qualidade.py
+++ b/qualidade.py
@@
 TOL = 0.01   # 1 centavo — muito acima do ruido de ponto flutuante, muito abaixo do relevante
+# sem estas tabelas a rodada nao e publicavel: as demais checagens sao condicionais e um
+# `tabs` degradado passaria com "QUALIDADE OK — 2 checagens criticas passaram".
+TABELAS_OBRIGATORIAS = ("run_meta", "run_obra", "run_subbacia", "run_ano", "run_cidade_ano")
@@
     rmc = tabs.get("run_meta_cobertura")
 
+    # ---- 0. as tabelas obrigatorias existem e nao estao vazias ------------------
+    faltando = [t for t in TABELAS_OBRIGATORIAS
+                if tabs.get(t) is None or len(tabs[t]) == 0]
+    add("Materializacao: tabelas obrigatorias presentes", not faltando,
+        f"ausentes/vazias: {faltando}" if faltando else "ok")
+
+    # ---- 0b. run_id unico e igual em TODAS as tabelas ---------------------------
+    # divergencia aqui viraria violacao de FK dentro da transacao de publicacao
+    # (ERRO tecnico opaco) em vez de FALHOU_QUALIDADE explicado.
+    rids = set()
+    for nome, df in tabs.items():
+        if nome.startswith("snapshot__") or df is None or len(df) == 0:
+            continue
+        if "run_id" in getattr(df, "columns", []):
+            rids |= set(df["run_id"].dropna().unique())
+    add("run_id: unico em todas as tabelas", len(rids) == 1,
+        f"run_id(s) encontrados: {sorted(rids)}")
+
+    # ---- 0c. sem duplicatas nas PKs (barra aqui, nao no INSERT) -----------------
+    try:
+        from publicacao import CHAVES as _CHAVES
+    except Exception:
+        _CHAVES = {}
+    dups = []
+    for nome, chave in _CHAVES.items():
+        df = tabs.get(nome)
+        if df is None or not chave or not set(chave) <= set(df.columns):
+            continue
+        n = int(df.duplicated(subset=list(chave)).sum())
+        if n:
+            dups.append(f"{nome}({n})")
+    add("Chaves: sem duplicatas nas PKs", not dups,
+        f"duplicadas em: {dups}" if dups else "ok")
+
     # ---- 1. status do solver ---------------------------------------------------
@@
     if ra is not None and "excesso" in ra.columns:
         anos_estouro = int((ra["excesso"] > 1).sum())
         add("Orcamento: teto anual respeitado", anos_estouro == 0,
             f"{anos_estouro} ano(s) com estouro de teto")
+
+    # ---- 4b. o teto EXISTE (sem orcamento o motor usa INF e a checagem acima passa
+    #          trivialmente — plano irrestrito publicado como SUCESSO) -------------
+    if ra is not None and "teto" in ra.columns:
+        sem_teto = int((~ra["teto"].between(0, 1e17)).sum())
+        add("Orcamento: teto definido em todos os anos", sem_teto == 0,
+            f"{sem_teto} ano(s) sem teto (CAPEX ilimitado)")
```

### D7 — SQL portável e erro original preservado  *(corrige M3, M7)*

```diff
--- a/job_databricks.py
+++ b/job_databricks.py
@@
 def _ler_run_request(pg_url, run_id, schema="controle"):
     """Le a linha de run_request (parametros da rodada) como dict."""
     import pandas as pd
-    from sqlalchemy import create_engine
-    eng = create_engine(pg_url)
-    df = pd.read_sql(f'SELECT * FROM "{schema}".run_request WHERE run_id = %(rid)s',
-                     eng, params={"rid": run_id})
+    from sqlalchemy import create_engine, text
+    # bind no estilo do SQLAlchemy (:rid). O `%(rid)s` antigo so funcionava por acidente
+    # do paramstyle pyformat do psycopg2 — pandas embrulha a string em text().
+    eng = create_engine(pg_url)
+    try:
+        df = pd.read_sql(text(f'SELECT * FROM "{schema}".run_request WHERE run_id = :rid'),
+                         eng, params={"rid": run_id})
+    finally:
+        eng.dispose()
@@
-    except Exception as e:                       # qualquer falha tecnica -> ERRO (nao vazio)
-        PUB.marcar_status_controle(pg_url, run_id, "ERRO", erro=f"{type(e).__name__}: {e}")
-        print("ERRO na rodada:\n" + traceback.format_exc())
-        raise
+    except Exception as e:                       # qualquer falha tecnica -> ERRO (nao vazio)
+        try:
+            PUB.marcar_status_controle(pg_url, run_id, "ERRO", erro=f"{type(e).__name__}: {e}")
+        except Exception:                        # banco fora do ar e a causa mais provavel
+            print("ATENCAO: falhou tambem ao marcar ERRO:\n" + traceback.format_exc())
+        print("ERRO na rodada:\n" + traceback.format_exc())
+        raise                                    # o `raise` nu preserva o traceback original
```

### D8 — DDL de input: chaves, tipos e limpeza  *(corrige A5, M1, M2 e C4 parte 3/3)*

Migration incremental (não recria nada; roda uma vez):

```sql
-- ---- limpeza das colunas de comentario que vazaram do Excel de amostra (M2) -------
ALTER TABLE input.cidade_operacional DROP COLUMN IF EXISTS "Unnamed: 3";
ALTER TABLE input.fator_esgoto      DROP COLUMN IF EXISTS "Unnamed: 4";
-- (as duas colunas cujo NOME e uma frase de documentacao: idem, pelo nome exato)

-- ---- tipos (M1): populacao e numero; fator de crescimento e continuo; % pode ter casa
ALTER TABLE input.subbacia_operacional
    ALTER COLUMN universo_populacao     TYPE double precision USING universo_populacao::double precision,
    ALTER COLUMN populacao_atual        TYPE double precision USING populacao_atual::double precision,
    ALTER COLUMN populacao_novas_obras  TYPE double precision USING populacao_novas_obras::double precision,
    ALTER COLUMN potencial_crescimento  TYPE double precision;   -- era integer (arredondava 1,5 -> 2)
ALTER TABLE input.cts_operacional
    ALTER COLUMN universo_populacao     TYPE double precision USING universo_populacao::double precision,
    ALTER COLUMN populacao_atual        TYPE double precision USING populacao_atual::double precision,
    ALTER COLUMN populacao_novas_obras  TYPE double precision USING populacao_novas_obras::double precision;
ALTER TABLE input.metas_cobertura ALTER COLUMN cobertura_pct TYPE double precision;
ALTER TABLE input.fator_esgoto    ALTER COLUMN cobertura_pct TYPE double precision;

-- ---- chaves (A5): duplicata no cadastro corrompe o plano em silencio -------------
--   subbacia-operacional: a ultima linha vence (some uma sub-bacia)
--   componentes-*-capex : a obra e DUPLICADA (CAPEX conta duas vezes)
ALTER TABLE input.unidade_regional          ADD PRIMARY KEY (unidade_id);
ALTER TABLE input.regional_superintendencia ADD PRIMARY KEY (superintendencia_id);
ALTER TABLE input.superintendencia_cidade   ADD PRIMARY KEY (cidade_id);
ALTER TABLE input.cidade_sistema            ADD PRIMARY KEY (sistema_id);
ALTER TABLE input.sistema_topologia         ADD PRIMARY KEY (componente_sistema_id);
ALTER TABLE input.cidade_operacional        ADD PRIMARY KEY (cidade_id);
ALTER TABLE input.subbacia_operacional      ADD PRIMARY KEY (sub_bacia);
ALTER TABLE input.componentes_subbacias_capex ADD PRIMARY KEY (sub_bacia, componente);
ALTER TABLE input.ete_capex                 ADD PRIMARY KEY (ete_id);
ALTER TABLE input.regional_operacional      ADD PRIMARY KEY (regional_id);
ALTER TABLE input.metas_cobertura           ADD PRIMARY KEY (cidade_id, ano);
ALTER TABLE input.fator_esgoto              ADD PRIMARY KEY (cidade_id, cobertura_pct);
ALTER TABLE input.subbacia_cts              ADD PRIMARY KEY (sub_bacia);
ALTER TABLE input.cts_operacional           ADD PRIMARY KEY (cts);
ALTER TABLE input.componentes_cts_capex     ADD PRIMARY KEY (cts, componente);

-- ---- FKs: SIM entre as tabelas de hierarquia (o motor navega por elas e uma quebra
--      vira sub-bacia orfa, que some do resultado sem erro). NAO em metas/fator, que
--      podem ser carregadas antes do cadastro da cidade.
ALTER TABLE input.regional_superintendencia
    ADD CONSTRAINT fk_sup_unidade  FOREIGN KEY (unidade_id)         REFERENCES input.unidade_regional(unidade_id);
ALTER TABLE input.superintendencia_cidade
    ADD CONSTRAINT fk_cid_sup      FOREIGN KEY (superintendencia_id) REFERENCES input.regional_superintendencia(superintendencia_id);
ALTER TABLE input.cidade_sistema
    ADD CONSTRAINT fk_sis_cidade   FOREIGN KEY (cidade_id)          REFERENCES input.superintendencia_cidade(cidade_id);
ALTER TABLE input.componentes_subbacias_capex
    ADD CONSTRAINT fk_comp_sub     FOREIGN KEY (sub_bacia)          REFERENCES input.subbacia_operacional(sub_bacia);
ALTER TABLE input.componentes_cts_capex
    ADD CONSTRAINT fk_comp_cts     FOREIGN KEY (cts)                REFERENCES input.cts_operacional(cts);
ALTER TABLE input.subbacia_cts
    ADD CONSTRAINT fk_subcts_sub   FOREIGN KEY (sub_bacia)          REFERENCES input.subbacia_operacional(sub_bacia);

-- ---- indices que o backend/front vao usar ----------------------------------------
CREATE INDEX IF NOT EXISTS ix_sub_cidade   ON input.componentes_subbacias_capex (sub_bacia);
CREATE INDEX IF NOT EXISTS ix_metas_cidade ON input.metas_cobertura (cidade_id);
CREATE INDEX IF NOT EXISTS ix_fator_cidade ON input.fator_esgoto (cidade_id);

-- ---- a aba `orcamento` que o motor le e nao existe no schema (C4) ----------------
CREATE TABLE IF NOT EXISTS input.orcamento (
    regional_id text PRIMARY KEY,
    valor_ano   double precision NOT NULL
);

-- ---- controle: coerencia de estados e rastro ------------------------------------
ALTER TABLE controle.run_status ADD CONSTRAINT ck_status
    CHECK (status IN ('PENDENTE','RODANDO','SUCESSO','FALHOU_QUALIDADE','ERRO'));
ALTER TABLE controle.run_status ADD CONSTRAINT fk_status_request
    FOREIGN KEY (run_id) REFERENCES controle.run_request(run_id);
```

> As PKs assumem que o cadastro é de **uma unidade por linha de sub-bacia**. Se
> `sub_bacia`/`cts` puderem repetir entre unidades, a PK tem de virar
> `(unidade_id, sub_bacia)` — e aí a coluna `unidade_id` precisa existir nessas tabelas.
> É a mesma decisão de M10 e vale confirmar com quem define o cadastro **antes** de aplicar.

### D9 — deixar a suíte verde  *(corrige A4)*

```diff
--- a/tests/test_nucleo.py
+++ b/tests/test_nucleo.py
@@
 def test_separabilidade_por_cidade_e_exata():
     solver_or_skip()   # tambem instala os shims de nome
-    import testes_otimizador as TT
+    # a suite legada `testes_otimizador` nao acompanha o pacote de producao; quando ela
+    # nao esta na sessao, PULA (mesma politica de `require_bank` e `solver_or_skip`).
+    TT = pytest.importorskip(
+        "testes_otimizador",
+        reason="suite legada `testes_otimizador.py` ausente neste pacote")
     cen = load_cts(True)
     assert TT.teste_separabilidade(cen), "a decomposicao por cidade deveria fechar (diff ~ 0)"
```

Sem mudança de semântica nem de valor golden: só troca `ModuleNotFoundError` por `skip`.
Resultado esperado: `30 passed, 1 skipped`.

---

## 3. Checklist do plano (§6), item a item

| # | Item | Veredito |
|---|---|---|
| 1 | Fronteira motor ≠ dados mantida? | **OK.** Varredura por `read_sql\|create_engine\|psycopg2\|sqlalchemy\|open(\|to_excel\|read_excel\|requests\.\|urllib` em `otimizador_capex_v62.py` e `otimizador_capex_cpsat63.py`: **zero ocorrências**. `ler_banco` importa `openpyxl` localmente (`v62:841`), o que é leitura de arquivo, mas está dentro do adaptador de leitura do próprio motor e é o caminho Excel que precisa continuar funcionando. Retrocompatibilidade preservada: nenhum diff acima toca `ler_banco`. |
| 2 | `ABAS_INPUT` cobre todas as abas de `ler_banco`? | **Não.** O motor lê 16 nomes de aba; `ABAS_INPUT` mapeia 15. Faltam **`orcamento`** (C4 — grave) e `sistema-operacional` (compat com bancos antigos, `v62:976` — pode ficar de fora, mas então deve estar em `ABAS_OPCIONAIS`). Os 15 nomes mapeados batem com os nomes de tabela do DDL. |
| 3 | DDL de input: tipos, PKs, índices, FKs? | **Não.** Zero PKs/FKs/NOT NULLs (A5), tipos errados (M1), colunas-lixo (M2), `input.orcamento` inexistente (C4). Diff D8. |
| 4 | Portão: falta checagem crítica? tolerância? crítico × aviso? | **Falta.** Ausência/vazio de tabela, `run_id` único, duplicata de PK e teto inexistente (A1, C4). Tolerância adequada hoje (B5). "Plano não-vazio" como `aviso` é decisão de negócio pendente (B4). Diff D6. |
| 5 | Transação na publicação (tudo-ou-nada)? | **Parcial.** `publicar_postgres` é atômico por chamada — DDL, DELETE e todos os `execute_values` num único `with conn:`. Mas o `SUCESSO` fica numa transação separada (A2) e `conn.close()` no `finally` impede compor (A2). Diffs D5/D5b. |
| 6 | `gravar_diagnostico` criado e testado? | **Criado** (`publicacao.py:298-318`), correto e idempotente (DELETE por `run_id` + `execute_values`). **Não testado** — nenhum teste da suíte cobre `publicacao.py`. Ver §5. |
| 7 | Secrets: nenhuma credencial no código? | **OK.** Só placeholder de docstring e `dbutils.secrets.get`. Ressalva: o formato aceito do `pg_url` não está documentado e os dois consumidores aceitam formatos diferentes (A3). |
| 8 | Retry/idempotência segura em todos os caminhos? | **Não.** C1 quebra a idempotência inteira: cada retry publica um conjunto novo. Depois de D1, sim — `DELETE ... WHERE run_id` + FK `ON DELETE CASCADE` cobrem o caminho de publicação, e `gravar_diagnostico`/`marcar_status_controle` já são idempotentes. |
| 9 | Fase 2b vale a pena já? | **Não agora.** Ver §4. |
| 10 | Empacotamento (wheel) e CI | **A fazer** (Fase 5, já marcada como pendente no plano). Pré-requisito: D9, senão o CI nasce vermelho. |

---

## 4. Fase 2b (`ler_banco` aceitando dict de DataFrames) — recomendação

**Não agora; depois de C1–C5.** O `.xlsx` temporário tem custos reais — exige `openpyxl` no
cluster, escreve no disco local do driver (`tempfile`, não DBFS), perde tipagem no caminho
Postgres→pandas→Excel→`openpyxl`→`float()` e impede filtrar por unidade no `SELECT`. Mas
nenhum desses custos é a causa de um único achado crítico acima, e a Fase 2b mexe na
assinatura de `ler_banco`, que é justamente o ponto que a retrocompatibilidade Excel e os
31 testes protegem.

Quando for feita, o caminho de menor risco é aditivo, sem tocar no corpo de `ler_banco`:

```python
def ler_banco(fonte, ...):
    # `fonte` = caminho .xlsx (como sempre) OU dict {nome_da_aba: DataFrame}
    if isinstance(fonte, dict):
        def L(*abas): ...   # mesma normalizacao de cabecalho, lendo do dict
    else:
        wb = load_workbook(fonte, data_only=True)
        def L(*abas): ...   # implementacao atual, intacta
```

O teste que garante a equivalência já existe em espírito: `_roundtrip_xlsx`
(`carregar_postgres.py:101`) prova que a materialização não altera o Cenário. A versão 2b
do mesmo teste — `ler_banco(xlsx)` vs `ler_banco(dict_de_dataframes)`, comparando
nós/obras/VPL/vazão — é o que autoriza a troca.

---

## 5. O que mais falta para produção (fora do escopo do checklist)

1. **`publicacao.py` não tem nenhum teste.** É o módulo que escreve no banco de produção,
   com 467 linhas, e a suíte não o toca. Um teste com psycopg2 em container (ou
   `testing.postgresql`) cobrindo publicar → republicar mesmo `run_id` → conferir que não
   duplicou é o teste de maior valor que falta no pacote — e é exatamente o que teria
   pegado C1.
2. **Nenhum teste cobre `job_databricks.py` nem `carregar_postgres.py`.** Um teste de
   `_params_para_ler_banco` comparando os defaults com
   `inspect.signature(M.ler_banco).parameters` teria pegado C2 e C3, e continuaria pegando
   a divergência se alguém mudar um default do motor no futuro.
3. **`ETE_FASEADA`, `FOCO_COBERTURA` e os outros ~18 parâmetros não têm contrato escrito.**
   O `README_producao.md` cita "os ~18 parâmetros da célula PARAMETROS" sem enumerar tipo,
   default e efeito. Com D2 rejeitando chaves desconhecidas, esse contrato passa a ser
   obrigatório para o backend — vale gerar a tabela a partir de `MAPA_PARAMS` +
   `inspect.signature(ler_banco)`.
4. **Ordem de aplicação sugerida:** D9 (verde) → D1 → D2 → D5/D5b → D4 → D3 → D6 → D7 →
   D8 (migration, com o DBA) . Rodar `pytest -q tests/` depois de cada bloco; os diffs D1–D7
   não tocam o motor, então os valores golden não devem mudar.

---

## 6. O que foi aplicado

Tudo na ordem acima. Nenhuma linha do motor (`otimizador_capex_v62.py`,
`otimizador_capex_cpsat63.py`) foi tocada, o caminho Excel (`ler_banco`) não mudou e nenhum
valor golden foi alterado.

| Arquivo | Mudança |
|---|---|
| `tests/test_nucleo.py` | D9 — `importorskip` no teste da suíte legada |
| `job_databricks.py` | D1 (`run_id=` na materialização) · D2 (`MAPA_PARAMS`, sem default próprio, chave desconhecida = erro) · D3 (guarda de orçamento) · D5b (publicação + SUCESSO num commit, `criar=False`) · D7 (`text()`+`:rid`, `eng.dispose()`, erro original preservado) · `MAX_TIME_S`/`WORKERS` por rodada · novo parâmetro `schema_pub` |
| `publicacao.py` | D5 — `_transacao()` com commit/rollback explícito; `_conectar` devolve `(conn, proprio)` e só fecha o que abriu; fallback SQLAlchemy removido; `pd.isna` legível (B2) |
| `carregar_postgres.py` | D4 — `ABAS_OPCIONAIS` + `_e_tabela_ausente` (só 42P01 é pulável); aba `orcamento` mapeada; `eng.dispose()`; nome de aba > 31 chars vira erro (B3) |
| `qualidade.py` | D6 — checagens 0 (tabelas obrigatórias), 0b (`run_id` único), 0c (duplicata de PK), 4b (teto definido) · **C6**: status do solver aceita `OTIMO`/`VIAVEL` |
| `ddl_input.sql` | D8 — reescrito para banco novo: PKs, FKs de hierarquia, tipos numéricos, `input.orcamento`, colunas-lixo viram `COMMENT ON`, `CHECK` de status |
| `ddl_input_migracao_01.sql` | **novo** — mesma coisa para banco que já existe, em uma transação, com consultas de diagnóstico |
| `tests/test_producao.py` | **novo** — 16 testes da camada de produção, sem Postgres |
| `requirements-prod.txt` | A6 — `matplotlib` |
| `README_producao.md` | formato do `pg_url`, contrato do `params`, migration em vez de DDL no caminho quente, contagem de testes |

### Verificações executadas

```
pytest -q tests/                       ->  61 passed, 13 skipped
```

Portão sobre uma materialização real (banco de teste CTS + CP-SAT, `run_id` fixo):

```
[OK] Materializacao: tabelas obrigatorias presentes   ok
[OK] run_id: unico em todas as tabelas                ['run_teste_002']
[OK] Chaves: sem duplicatas nas PKs                   ok
[OK] Status do solver                                 status=OTIMO | OBRIG 0/0
[OK] Orcamento: teto definido em todos os anos        0 ano(s) sem teto
... (12 checagens criticas)                           QUALIDADE OK
```

E os quatro casos negativos, cada um reprovando pela checagem certa: `teto_capex = INF`
(20 anos sem teto), `run_id` divergente (`['OUTRO','r1']`), duplicata na PK de `run_obra`,
`run_cidade_ano` vazia.

### O que continua sem verificação de execução

Nada nesta revisão tocou um Postgres de verdade. `_transacao`, o commit único de D5b, o
`DELETE`+`CASCADE` da republicação e as FKs/PKs do DDL estão **corretos por leitura, não por
teste**.

Os testes que provam isso já estão escritos — `tests/test_publicacao_postgres.py`, 12 testes —
mas **nunca foram executados**: não há Postgres nesta máquina (Docker CLI instalado, daemon
parado; nenhum `psql`/serviço local). Eles pulam sozinhos sem `OTIMIZADOR_PG_TESTE`, então o
CI offline continua verde. O que foi verificado offline: o módulo coleta, e `_ddl_controle`
extrai corretamente o trecho CONTROLE do `ddl_input.sql` trocando o schema. **Espere ajustes
na primeira execução real** — é a natureza de um teste de integração que nunca rodou.

O que eles cobrem: republicar o mesmo `run_id` não duplica · republicar com menos obras apaga
as antigas (cascade) · outra rodada não é afetada · falha no meio não grava nada · publicação
e SUCESSO no mesmo commit voltam juntos · a conexão do chamador não é fechada · upsert de
status · status fora do domínio é rejeitado pelo `CHECK` · diagnóstico idempotente.

Roteiro mínimo no banco local:

1. `psql -f ddl_input.sql` no banco novo;
2. carregar o cadastro de uma unidade pequena em `input.*`;
3. `INSERT` numa `controle.run_request` com `params` contendo pelo menos `UNIDADE` e
   `ORCAMENTO`;
4. `rodar(run_id, pg_url)` e conferir `controle.run_status`;
5. **rodar o MESMO `run_id` de novo** e conferir que `SELECT count(*) FROM public.otim_meta`
   continua 1 — é o teste da idempotência (C1) que só o banco prova.
