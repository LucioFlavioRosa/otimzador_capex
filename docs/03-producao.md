# 3. Colocar em produção

Público: quem instala e opera (plataforma/DevOps + engenheiro de dados).

---

## 3.1 Pré-requisitos

| Item | Versão | Nota |
|---|---|---|
| Python | 3.10+ | o pacote usa `from __future__ import annotations`; testado em 3.14 |
| Databricks Runtime | 13.x LTS ou superior | qualquer DBR com Python 3.10+ |
| Postgres | 12+ | Azure Database for PostgreSQL; usa `jsonb`, `ON CONFLICT`, `execute_values` |
| Rede | cluster → Postgres | *Private Endpoint* ou regra de firewall para a subnet do workspace |

Dependências (`requirements-prod.txt`):

```
ortools>=9.7          pandas>=1.5         openpyxl>=3.1
matplotlib>=3.5       sqlalchemy>=1.4     psycopg2-binary>=2.9
azure-servicebus>=7.11 (opcional)         pyarrow>=12 (opcional)
pytest>=7.0
```

`matplotlib` é obrigatório apesar de não desenhar nada em produção: `job_databricks` importa
`dashboard_otimizador_v2`, que a persistência usa para a explicabilidade. No DBR ele já vem
instalado; num venv limpo (CI) não.

---

## 3.2 Secrets

Databricks Secret Scope `otimizador`:

| Secret | Conteúdo |
|---|---|
| `pg_url` | `postgresql://user:senha@host:5432/otimizador` |
| `sb_conn` | connection string do Service Bus (opcional) |

```bash
databricks secrets create-scope otimizador
databricks secrets put-secret otimizador pg_url
databricks secrets put-secret otimizador sb_conn
```

> ⚠️ **O formato da `pg_url` importa.** Use `postgresql://…`. O prefixo de dialeto do
> SQLAlchemy (`postgresql+psycopg2://…`) **é rejeitado** por `psycopg2.connect`, e a mesma URL
> precisa servir aos dois consumidores: SQLAlchemy em `carregar_postgres` e `psycopg2` em
> `publicacao`. Se a senha tiver caractere especial, faça *percent-encoding*.

**Nunca** no código, em widget, em variável de ambiente do cluster ou em notebook.

---

## 3.3 Instalar o banco

### Banco novo

```bash
psql "$PG_URL" -f ddl_input.sql
```

Cria `input` (16 tabelas de cadastro) e `controle` (`run_request`, `run_status`,
`run_diagnostico`), com PKs, FKs da hierarquia, índices e o `CHECK` dos estados.

### Banco que já existe na versão antiga (sem PKs/FKs)

```bash
psql "$PG_URL" -f ddl_input_migracao_01.sql
```

Roda em **uma transação**: ou aplica tudo, ou nada. Se algum `ALTER` falhar, quase sempre é
**dado sujo** — duplicata na futura PK ou órfão na futura FK. As consultas de diagnóstico
estão comentadas no fim do próprio arquivo; rode-as antes:

```sql
SELECT sub_bacia, componente, count(*) FROM input.componentes_subbacias_capex
 GROUP BY 1,2 HAVING count(*) > 1;

SELECT c.* FROM input.componentes_subbacias_capex c
  LEFT JOIN input.subbacia_operacional s USING (sub_bacia) WHERE s.sub_bacia IS NULL;
```

### Tabelas de resultado (`public.otim_*`)

```bash
psql "$PG_URL" -f ddl_resultado.sql
```

O arquivo já acompanha o pacote. Ele é **gerado** — reflete exatamente o que a publicação
escreve — e se precisar regerar (coluna nova na materialização, por exemplo):

```bash
python gerar_ddl_resultado.py
```

O gerador materializa cinco cenários das fixtures e fica com o tipo mais específico de cada
coluna; ver [`06-dicionario-resultado.md`](06-dicionario-resultado.md) §6.10 para o porquê.
Equivalente inline, se você quiser fazer na mão:

```python
import matplotlib; matplotlib.use("Agg")
import dashboard_otimizador_v2 as D, persistencia as P, publicacao as PUB
import otimizador_capex_v62 as M
D.set_engine(M); P.set_engine(M, D)

cen  = M.ler_banco("tests/fixtures/banco_teste_CTS_poc_v2.xlsx", orcamento=1e9)
plano = {oid: max(0, int(o.inicio_min)) for oid, o in cen.obras.items() if o.eh_aegea()}
tabs = P.materializar(cen, M.avaliar(cen, plano), run_id="ddl", banco="ddl")

open("ddl_resultado.sql", "w", encoding="utf-8").write(PUB.ddl_postgres(tabs, schema="public"))
```

```bash
psql "$PG_URL" -f ddl_resultado.sql
```

Cria 14 tabelas `otim_*` com PK, **FK para `otim_meta` com `ON DELETE CASCADE`**, índices e as
3 views.

> **Por que gerar em vez de escrever à mão:** o cascade é o que faz a republicação de um
> `run_id` ficar limpa, e os índices são os que o front consulta. Um DDL escrito à mão
> divergindo do gerado é a origem mais provável de "republicou e duplicou".

### Permissões

```sql
-- front
GRANT USAGE ON SCHEMA input TO front_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA input TO front_app;

-- backend
GRANT USAGE ON SCHEMA controle, public TO backend_app;
GRANT INSERT, SELECT ON controle.run_request TO backend_app;
GRANT SELECT ON controle.run_status, controle.run_diagnostico TO backend_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO backend_app;

-- job
GRANT USAGE ON SCHEMA input, controle, public TO job_otimizador;
GRANT SELECT ON ALL TABLES IN SCHEMA input TO job_otimizador;
GRANT SELECT ON controle.run_request TO job_otimizador;
GRANT INSERT, UPDATE, DELETE, SELECT ON controle.run_status, controle.run_diagnostico TO job_otimizador;
GRANT ALL ON ALL TABLES IN SCHEMA public TO job_otimizador;
```

O job **não precisa** de permissão de DDL: ele publica com `criar=False`. Isso é proposital —
ver §3.8.

---

### Validar a instalação

Antes de tocar no Databricks, prove o banco:

```bash
python smoke_test_postgres.py --pg "$PG_URL" --schemas-reais --manter
```

Ele aplica os dois DDL, carrega uma fixture, roda o job fim a fim, confere as reconciliações
e republica a mesma `run_id` para provar que não duplica. Falha com código 1 e diz onde.
Se a carga do cadastro falhar por PK ou FK, é dado duplicado ou órfão — as consultas de
diagnóstico estão no fim de `ddl_input_migracao_01.sql`.

---

## 3.4 Instalar o código no Databricks

Hoje o pacote é **flat**: os módulos se importam como irmãos (`import publicacao`), sem
namespace de pacote. Duas formas de instalar:

**(a) Workspace Files / Repos** — copie a pasta e adicione ao `sys.path`:

```python
import sys; sys.path.append("/Workspace/Repos/otimizador/Otimizador_Producao")
from job_databricks import rodar
```

**(b) Wheel** *(Fase 5, ainda não feita)* — empacotar com módulos nomeados
(`engine`/`dados`/`qualidade`/`persistencia`/`publicacao`/`job`) e instalar como library do
job. É a forma recomendada para produção de verdade; enquanto não existe, (a) funciona.

---

## 3.5 O entrypoint

Notebook de **uma célula**, ou entrypoint do wheel:

```python
from job_databricks import rodar

resultado = rodar(
    run_id      = dbutils.widgets.get("run_id"),
    pg_url      = dbutils.secrets.get("otimizador", "pg_url"),
    service_bus = dbutils.secrets.get("otimizador", "sb_conn"),   # opcional
    blob        = "abfss://dados@conta.dfs.core.windows.net/otimizador/",  # opcional
)
print(resultado)      # {"run_id": ..., "status": "SUCESSO"|"FALHOU_QUALIDADE", ...}
```

Assinatura completa:

```python
rodar(run_id, pg_url,
      schema_input="input", schema_ctrl="controle", schema_pub="public",
      service_bus=None, blob=None, webhook=None, webhook_token=None,
      max_time_s=300, workers=8)
```

`max_time_s` e `workers` são o **default do job**; a `run_request` pode sobrescrever por rodada
com `MAX_TIME_S` / `WORKERS`.

`blob` liga a cópia integral em parquet — **inclusive o snapshot do cadastro, que não vai
para o Postgres**. É a camada de reprodução: permite refazer meses depois exatamente a mesma
rodada. Sem ele, `otim_meta.blob_uri` fica nulo. `webhook` notifica o backend por HTTP, além
do Service Bus; os dois convivem.

### Configuração do job

| Item | Recomendação |
|---|---|
| Tipo | Job cluster (efêmero), não all-purpose |
| Nós | **single node** — o CP-SAT é multi-thread num processo só; worker não ajuda |
| Cores | ≥ `WORKERS` (default 8) |
| Widget | `run_id` (string, obrigatório) |
| Retries | **0 ou 1.** O job já é idempotente, mas retry automático mascara falha de dados; prefira retry deliberado pelo operador |
| Timeout | `MAX_TIME_S` + folga para carga e publicação (ex.: 900 s para `MAX_TIME_S=300`) |
| Alertas | notificar em falha — cobre **`ERRO`** apenas: o job re-levanta a exceção depois de marcar o status, e o run aparece como falho no Databricks |

> ⚠️ **`FALHOU_QUALIDADE` não faz o job falhar.** `rodar()` retorna normalmente nesse caso —
> é um resultado (a rodada foi calculada, mas reprovou), não uma falha técnica. O alerta do
> Databricks **não dispara**. Quem precisa detectar reprovação de qualidade tem de monitorar
> `controle.run_status`, não o estado do job. Se você preferir que também falhe o run, é uma
> linha em `job_databricks.rodar`: levantar em vez de retornar no ramo `if not ok`.

---

## 3.6 Operar

### Consultas do dia a dia

```sql
-- estado de uma rodada
SELECT status, erro, atualizado_em FROM controle.run_status WHERE run_id = :run;

-- por que a qualidade reprovou
SELECT checagem, nivel, detalhe FROM controle.run_diagnostico
 WHERE run_id = :run AND ok = false ORDER BY nivel;

-- rodadas travadas há mais de 2h
SELECT run_id, status, atualizado_em FROM controle.run_status
 WHERE status = 'RODANDO' AND atualizado_em < now() - interval '2 hours';

-- as últimas 20 rodadas
SELECT run_id, rotulo, usuario, data_hora, vpl, capex_total, obras_construidas
  FROM public.otim_vw_historico LIMIT 20;

-- o cadastro de fato usado numa rodada (params efetivos)
SELECT params_extra FROM public.otim_meta WHERE run_id = :run;
```

### Reprocessar

Dispare o **mesmo `run_id`** de novo. Tudo é idempotente: a publicação apaga e regrava, o
diagnóstico apaga e regrava, o status é upsert. Não é preciso limpar nada antes.

### Apagar uma rodada

```sql
DELETE FROM public.otim_meta WHERE run_id = :run;   -- CASCADE leva os detalhes
```

---

## 3.7 Runbook de falhas

| Sintoma | Causa provável | Ação |
|---|---|---|
| `ERRO: run_request nao encontrada` | job disparado antes do commit do `INSERT`, ou `run_id` divergente | backend: commitar antes de disparar |
| `ERRO: ValueError: run_request.params com chaves desconhecidas` | typo/caixa na chave | corrigir o `params`; a mensagem lista as aceitas |
| `ERRO: sem teto anual de CAPEX para [...]` | sem `ORCAMENTO` no `params` e sem linha em `input.orcamento`; ou só `ORCAMENTO_TOTAL`, que não define teto anual | informar `ORCAMENTO`, ou preencher `input.orcamento` |
| `ERRO: falha ao ler input.<tabela>: permission denied` | `GRANT` faltando | §3.3 |
| `ERRO: input incompleto no Postgres: falta 'subbacia_operacional'` | cadastro vazio ou schema errado | conferir carga do `input` e `schema_input` |
| `ERRO: OperationalError: could not connect` | firewall/Private Endpoint, ou `pg_url` no formato do SQLAlchemy | §3.2 |
| `ERRO: OverflowError: cannot convert float infinity to integer` | teto infinito chegou ao CP-SAT | não deveria mais ocorrer (guarda no job); se ocorrer, o teto veio infinito por outro caminho |
| `FALHOU_QUALIDADE: Status do solver` | solver não achou solução viável | teto pequeno demais, ou obrigatórias inviáveis na janela |
| `FALHOU_QUALIDADE: Chaves: sem duplicatas nas PKs` | cadastro duplicado | corrigir `input` (as PKs do DDL novo previnem) |
| `FALHOU_QUALIDADE: Materializacao: tabelas obrigatorias presentes` | resultado degradado | investigar; é sintoma, não causa |
| `FALHOU_QUALIDADE: Orcamento: teto definido em todos os anos` | rodada sem teto em algum ano | conferir `ORCAMENTO` (dict por ano cobre todos os anos da janela?) |
| Status parado em `RODANDO` | job morreu sem chegar ao `except` (OOM, cluster derrubado) | ver o run no Databricks; redisparar o mesmo `run_id` |
| Rodada demora demais | cenário grande + `MAX_TIME_S` alto | aumentar cores, ou reduzir `MAX_TIME_S` (CP-SAT devolve `VIAVEL(limite de tempo)`, que é aceito) |

**Como ler o log do driver:** o job imprime o relatório do portão inteiro (`qualidade.imprimir`)
antes de publicar, com `[OK]`/`[FALHA]` por checagem, e o traceback completo em caso de erro.
Comece por aí.

---

## 3.8 O que **não** fazer

- **Não ligue `criar=True` na publicação.** O job publica com `criar=False` de propósito. Com
  `True`, a DDL inteira roda dentro da transação de publicação a cada rodada: toma locks, pode
  bloquear leituras do front, e `CREATE TABLE IF NOT EXISTS` **não** corrige coluna nova — o
  `INSERT` é que falha, com erro obscuro. Mudança de esquema é migration.
- **Não reutilize `run_id` para parâmetros diferentes.** Republicar **apaga** o resultado
  anterior.
- **Não rode duas vezes o mesmo `run_id` em paralelo.** Idempotência protege repetição
  sequencial, não concorrência: dois jobs publicando o mesmo `run_id` ao mesmo tempo podem
  deadlock no `DELETE`.
- **Não coloque credencial em widget.** Widget é visível no histórico do job.
- **Não edite `ddl_input.sql` sem passar pela migration.** Banco novo e banco existente têm de
  convergir para o mesmo esquema.

---

## 3.9 Pendências assumidas

| Pendência | Impacto | Quando encarar |
|---|---|---|
| Wheel + CI (Fase 5) | instalação manual; sem gate automático de código | antes do primeiro deploy "de verdade" |
| Fase 2b (dict de DataFrames) | `.xlsx` temporário no driver | depois de validado em produção |
| `input` sem discriminador de unidade | cada rodada lê o cadastro inteiro | quando o cadastro nacional crescer |
| `psycopg2.sql.Identifier` | schema/tabela/coluna por f-string | manutenção |
| Testes de `publicacao.py` nunca executados | ver `04-testes-executar.md` §4 | **primeira coisa** ao subir o banco |

---

Próximo: **`04-testes-executar.md`**.
