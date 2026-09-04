# Otimizador de CAPEX — Pacote de Produção (Databricks + Postgres)

Pasta **completa e autossuficiente** para rodar o pipeline em produção: **ler do Postgres →
otimizar → testar (portão de qualidade) → salvar no Postgres**. Organizada em camadas
(DDD): entrada única em `main.py`, código no pacote `otimizador/` —
`dominio/` (motor puro + portão) · `aplicacao/` (orquestração) · `infraestrutura/`
(Postgres, materialização, publicação, DDLs) · `apresentacao/` (contrato de leitura,
explicabilidade). O mapa de cada camada está em `otimizador/__init__.py`.

> 🚀 **Sem acesso ao Databricks?** [`docs/07-rodar-local.md`](docs/07-rodar-local.md) leva do
> clone à execução do job inteiro na sua máquina. Comece por `python main.py experimento`.
>
> 📚 **Documentação detalhada em [`docs/`](docs/README.md)** — visão geral · integração com o
> backend · colocar em produção · execução dos testes · o que os testes cobrem. Este README é
> o resumo de uma página; comece por `docs/README.md` se você chegou agora.

## Arquivos

```
main.py                  entrada única:  rodar | experimento | smoke | gerar-ddl
otimizador/
  dominio/               motor puro, solver, portão de qualidade, contrato do resultado
  aplicacao/             job de produção (Databricks) e experimentos locais
  infraestrutura/        adaptadores de I/O (Postgres, materialização, publicação) + sql/
  apresentacao/          contrato de leitura das telas e explicabilidade
scripts/                 ferramentas de operação (smoke, gerador de DDL)
tests/                   suíte de regressão
```

| Camada | Arquivo | Papel |
|---|---|---|
| **domínio** | `otimizador/dominio/otimizador_capex_v62.py` | carga do banco, modelo, `avaliar`. Sem I/O. |
| **domínio** | `otimizador/dominio/otimizador_capex_cpsat63.py` | solver OR-Tools (geração de colunas por cidade) |
| **domínio** | `otimizador/dominio/qualidade.py` | portão por rodada, antes de publicar |
| **domínio** | `otimizador/dominio/contrato_resultado.py` | as 14 tabelas publicadas, PKs e índices |
| **aplicação** | `otimizador/aplicacao/job_databricks.py` | orquestração fim-a-fim |
| **aplicação** | `otimizador/aplicacao/experimentos_local.py` | rodadas locais (`main.py experimento`) |
| **infraestrutura** | `otimizador/infraestrutura/persistencia.py` | materializa a rodada em 14 tabelas `run_*` |
| **infraestrutura** | `otimizador/infraestrutura/publicacao.py` | DDL de resultado, escrita idempotente, status, diagnóstico |
| **infraestrutura** | `otimizador/infraestrutura/carregar_postgres.py` | Postgres (input) → **Cenário** (reusa o motor) |
| **infraestrutura** | `otimizador/infraestrutura/sql/ddl_input.sql` | tabelas de input + controle |
| **infraestrutura** | `otimizador/infraestrutura/sql/ddl_resultado.sql` | `public.otim_*` — gerado por `main.py gerar-ddl` |
| **apresentação** | `otimizador/apresentacao/leitor_v2.py` | como o front reconstrói as telas (lado de consumo) |
| **apresentação** | `otimizador/apresentacao/dashboard_otimizador_v2.py` | usado pela persistência (explicabilidade) |
| **Docs** | `docs/` | os sete documentos do pacote; `docs/historico/` guarda plano e revisão antigos |

## Ordem do fluxo (uma rodada)

```
job_databricks.rodar(run_id, pg_url, service_bus)
  1. lê controle.run_request            (parâmetros da célula PARAMETROS)  ← antes do status
  2. marca controle.run_status = RODANDO
  3. carregar_postgres(...)             -> Cenário   (lê schema `input`)
     + exige teto anual de CAPEX        (ORCAMENTO no params ou input.orcamento)
  4. cpsat63.resolver_por_sistema(...)  -> otimiza
  5. persistencia.materializar(..., run_id=run_id)  -> tabelas run_*
  6. qualidade.checar(...)              -> PORTÃO  (falhou? grava diagnóstico, marca FALHOU_QUALIDADE, NÃO publica)
  7. publicacao.publicar(pg=..., status_controle=(run_id, "controle"), criar_schema=False)
         blob (se configurado) -> run_* + SUCESSO no MESMO commit -> notifica
```

## Setup (uma vez)

1. **Secrets** no Databricks Secret Scope `otimizador`: `pg_url` (conexão Postgres), `sb_conn`
   (Service Bus, opcional). Nunca no código.
   **Formato do `pg_url`:** `postgresql://user:senha@host:5432/otimizador`. Precisa servir aos
   dois consumidores — SQLAlchemy (`carregar_postgres`) e `psycopg2.connect` (`publicacao`) —
   e o `postgresql+psycopg2://` do SQLAlchemy é rejeitado pelo psycopg2.
2. **Schemas/tabelas** no Postgres, nesta ordem:
   - `otimizador/infraestrutura/sql/ddl_input.sql` — cria `input.*` (cadastro) e `controle.*` (run_request/status/diagnostico).
     Banco que já existe na versão antiga (sem PKs/FKs): rode `otimizador/infraestrutura/sql/ddl_input_migracao_01.sql`.
   - `otimizador/infraestrutura/sql/ddl_resultado.sql` — cria `public.otim_*` (14 tabelas + 3 views). Já acompanha o pacote;
     regere com `python main.py gerar-ddl` se a materialização mudar. Aplique como
     **migration**: o job publica com `criar_schema=False`, DDL não roda no caminho quente.
3. **Permissões:** front escreve `input`; backend escreve `controle.run_request`; o job escreve
   `public.otim_*` e `controle.run_status/diagnostico`; o front lê `public`.

## Disparar uma rodada (backend)

1. Grava o cadastro nas tabelas `input.*` (telas de cadastro).
2. `INSERT` em `controle.run_request` com `run_id` + `params` (JSONB = os parâmetros da célula
   PARAMETROS: `UNIDADE`, `ORCAMENTO`, `BASE_RECEITA`, `USAR_CTS`, `COBERTURA_SO_RESIDENCIAL`, ...).

   Contrato do `params` (validado em `job_databricks._params_para_ler_banco`):
   - chaves aceitas = `MAPA_PARAMS` + `CHAVES_DO_JOB` (`USUARIO`, `MAX_TIME_S`, `WORKERS`);
   - **chave desconhecida é erro**, não silêncio (um `orcamento` minúsculo faria a rodada sair
     sem teto de CAPEX);
   - **chave ausente usa o default do `ler_banco`** — o job não inventa default próprio, senão
     o mesmo `params` daria planos diferentes no job e no notebook;
   - é preciso **teto anual** de CAPEX: `ORCAMENTO` no `params` **ou** a tabela
     `input.orcamento`. `ORCAMENTO_TOTAL` sozinho **não basta** — ele limita o total da
     janela, mas a restrição anual do solver lê outro campo e estouraria com teto infinito;
   - `ORCAMENTO` por ano pode vir do JSONB como `{"2026": ...}`: o job converte a chave
     para `int`, que é o formato que o motor reconhece como cronograma.
3. Dispara o job (Databricks Jobs API) **ou** publica na fila Service Bus.

No Databricks, o entrypoint (notebook de 1 célula ou wheel):

```python
from otimizador.aplicacao.job_databricks import rodar
rodar(run_id=dbutils.widgets.get("run_id"),
      pg_url=dbutils.secrets.get("otimizador","pg_url"),
      service_bus=dbutils.secrets.get("otimizador","sb_conn"))
```

## Operar (engenheiro humano)

- **Status:** `SELECT status, erro, atualizado_em FROM controle.run_status WHERE run_id = '...';`
- **Por que falhou a qualidade:** `SELECT * FROM controle.run_diagnostico WHERE run_id = '...' AND ok = false;`
- **Reprocessar:** só enquanto `run_status` **não** for `SUCESSO` — aí rode o mesmo `run_id` de
  novo, que tudo é idempotente (Postgres apaga e regrava; blob substitui a partição da rodada).
  Depois do `SUCESSO`, gere um `run_id` novo: republicar apaga o resultado que já foi visto, e o
  cadastro pode ter mudado no meio. Isso depende de `materializar(..., run_id=run_id)`: é o
  `run_id` da rodada que liga `controle.*` a `public.otim_*` e é a chave do `DELETE` da
  publicação e da partição do blob.
- **Publicação e status entram no mesmo commit:** `public.otim_*` + `controle.run_status =
  SUCESSO` são uma transação só, então o estado observável nunca fica dessincronizado do dado.

## Testes

- **Qualidade por rodada:** `otimizador.dominio.qualidade.checar()` roda no job, antes de salvar.
- **Regressão de código (CI):** `pytest` na pasta `tests/` — deve estar verde antes do deploy.

```bash
pip install -r requirements-prod.txt
pytest tests/            # 127 testes: 114 passed, 13 skipped (12 pedem Postgres, 1 é a suíte legada)
```

`tests/test_producao.py` cobre a camada de produção (tradução de `run_request.params`, portão
de qualidade, propagação do `run_id`) sem precisar de Postgres. Um teste dedicado compara o mapa
de parâmetros do job com `inspect.signature(ler_banco)`: default do job que divirja do motor faz
a mesma rodada render planos diferentes aqui e no notebook.

### Smoke test contra um Postgres de verdade

```bash
docker run --rm -d -p 5433:5432 -e POSTGRES_PASSWORD=teste --name pg-otim postgres:16
python main.py smoke --pg "postgresql://postgres:teste@localhost:5433/postgres"
```

DDL → carga do cadastro → `rodar()` → reconciliações no banco → **republica a mesma `run_id`**
e confere que nada duplicou. É o primeiro comando a rodar quando existir um banco.

### Testes automatizados contra um Postgres de verdade

`tests/test_publicacao_postgres.py` cobre o que não dá para provar offline: idempotência da
republicação, atomicidade da transação, `ON DELETE CASCADE`, upsert de status e o `CHECK` dos
estados. **Pulam sozinhos** se `OTIMIZADOR_PG_TESTE` não estiver definida — o CI offline
continua verde.

```bash
docker run --rm -d -p 5433:5432 -e POSTGRES_PASSWORD=teste --name pg-otim postgres:16
export OTIMIZADOR_PG_TESTE="postgresql://postgres:teste@localhost:5433/postgres"   # PowerShell: $env:OTIMIZADOR_PG_TESTE=...
pytest tests/test_publicacao_postgres.py -v
docker rm -f pg-otim
```

Os testes criam os schemas `otim_teste_pub` e `otim_teste_ctrl` e os derrubam no fim — não
encostam em `public`, `input` nem `controle`. O DDL de controle sai do próprio `otimizador/infraestrutura/sql/ddl_input.sql`
(só trocando o schema), então mudança no DDL é exercitada sem editar o teste.
