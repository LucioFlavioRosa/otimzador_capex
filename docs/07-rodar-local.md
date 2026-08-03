# 7. Rodar tudo na sua máquina, sem Databricks

Público: o desenvolvedor que vai pegar o projeto **antes** de ter acesso ao Databricks.

A boa notícia: **o Databricks não é necessário para nada disso.** `job_databricks.rodar()` é
uma função Python comum — `dbutils` só aparece na docstring. O que o Databricks entrega é
cluster e agendamento; o cálculo, a publicação e o portão de qualidade rodam igual no seu
notebook.

Três níveis, do mais rápido ao mais completo:

| Nível | O que exercita | Precisa de |
|---|---|---|
| **A** — otimizador puro | motor + solver + portão de qualidade | só Python |
| **B** — pipeline completo | tudo do A + leitura e escrita no Postgres | + Docker |
| **C** — job de produção | o mesmo `rodar()` que o Databricks chama | + Docker |

Faça na ordem. Cada nível leva minutos.

---

## 7.0 Antes de tudo

```bash
git clone https://github.com/LucioFlavioRosa/otimzador_capex.git
cd otimzador_capex
python -m pip install -r requirements-prod.txt
python -m pytest -q tests/
```

Esperado: **69 passed, 13 skipped**. Os 13 skips são normais — 12 precisam de Postgres
(nível B) e 1 precisa de uma suíte legada que não acompanha o pacote. Detalhes em
[`04-testes-executar.md`](04-testes-executar.md).

Se algo falhar aqui, **pare e resolva** antes de seguir: a suíte verde é o chão de tudo o
que vem depois.

---

## Nível A — o otimizador, sem banco nenhum

### A.1 Uma primeira rodada

```bash
python main.py experimento
```

Isso carrega um banco de teste `.xlsx`, roda o solver OR-Tools, materializa as 14 tabelas em
memória e passa o portão de qualidade. Saída:

```
  status do solver     OTIMO | obrig 0/0
  VPL                          R$ 107.575.039
  CAPEX                          R$ 6.476.000
  obras construidas                     28/28
  sub-bacias faturando                    6/6
  cobertura final                        94.1%
  metas nao atingidas                     0/4
  tempo                                   0.2s
```

Seguido do relatório do portão, com as 14 checagens críticas. **Esse relatório é o mesmo que
roda em produção antes de publicar** — se ele reprova, a rodada não vai para o banco.

### A.2 Os bancos disponíveis

```bash
python main.py experimento --listar-bancos
```

Três fixtures acompanham o pacote, cada uma exercitando um aspecto: `cts` (o default, com
Coletor de Tempo Seco), `sem-cts` (mix de WACC) e `classe` (parcela industrial e cidades que
medem cobertura em economias e em população). São pequenas e sintéticas — nenhum dado de
cliente está versionado.

### A.3 Mexer nos parâmetros

```bash
python main.py experimento --orcamento 2e6            # teto anual apertado
python main.py experimento --foco-cobertura 1.0       # cobertura acima de VPL
python main.py experimento --sem-cts                  # CTS agregada, não como nó
python main.py experimento --banco classe --so-residencial
python main.py experimento --ete-faseada              # ETE vira K obras-módulo
```

Estes são exatamente os parâmetros que, em produção, chegam no `params` da `run_request` —
com o nome em maiúsculas (`ORCAMENTO`, `FOCO_COBERTURA`, `USAR_CTS`…). A tabela completa
está em [`02-integracao-backend.md`](02-integracao-backend.md) §2.3.

### A.4 Comparar cenários — é aqui que se aprende o modelo

```bash
python main.py experimento --comparar orcamento
```

```
  (construir tudo custa R$ 6.476.000, com pico de R$ 6.476.000 num unico ano)

  cenario                                VPL           CAPEX   obras  cobert.  metas!
  25% do pico (R$ 1.619.000)   R$ 43.769.825    R$ 3.140.000   14/28    64.7%       2
  50% do pico (R$ 3.238.000)  R$ 100.696.237    R$ 6.476.000   28/28    94.1%       0
  75% do pico (R$ 4.857.000)  R$ 107.575.039    R$ 6.476.000   28/28    94.1%       0
  100% do pico (R$ 6.476.000) R$ 107.575.039    R$ 6.476.000   28/28    94.1%       0
```

Lê-se assim: com um quarto do teto necessário, metade das obras fica de fora, o VPL cai 60% e
duas metas de cobertura não são atingidas. A partir de 75% do pico, o teto deixa de
restringir — daí para cima nada muda.

As outras dimensões:

```bash
python main.py experimento --comparar foco --orcamento 2e6   # VPL x cobertura
python main.py experimento --comparar cts                    # quanto a CTS custa em VPL
python main.py experimento --banco classe --comparar industrial
```

> **Cuidado com o teto folgado.** Se o orçamento não restringe, **todos os cenários dão o
> mesmo número** — o solver constrói tudo e nenhum outro parâmetro muda a escolha. O script
> avisa quando isso acontece. É a confusão nº 1 de quem começa: mexe no `--foco-cobertura`,
> não vê diferença, e conclui que o parâmetro não funciona.

### A.5 Ver o plano e as tabelas

```bash
python main.py experimento --detalhe                 # obra a obra: início, pronta, capex
python main.py experimento --salvar resultados/      # as 14 tabelas em CSV
```

Abra `resultados/run_obra/dados.csv`: cada obra tem `construida`, `data_inicio`,
`data_pronta` e — o mais interessante — `status`, `categoria_motivo`, `motivo` e
`elo_que_trava` para as que ficaram de fora. O dicionário de todas as colunas está em
[`06-dicionario-resultado.md`](06-dicionario-resultado.md).

As pastas `snapshot__*` são a cópia congelada do banco de entrada. Em produção elas vão
para o blob, nunca para o Postgres.

### A.6 O atalho `--build-all`

```bash
python main.py experimento --build-all
```

Constrói tudo no início, sem solver: instantâneo e determinístico. Serve de **piso de
comparação** — o solver nunca pode ficar pior que ele, e é isso que
`test_regressao_golden.py` verifica. Duas ressalvas: o build-all **ignora o teto**, então não
adianta comparar orçamentos com ele; e o portão vai reprovar a checagem "Status do solver"
por construção, já que não houve solver (o script avisa).

---

## Nível B — com Postgres, sem Databricks

### B.1 Subir um Postgres descartável

```bash
docker run --rm -d -p 5433:5432 -e POSTGRES_PASSWORD=teste --name pg-otim postgres:16
```

Sem Docker? Qualquer Postgres 12+ serve — inclusive um instalado localmente. Só aponte a URL.

### B.2 O smoke test

```bash
python main.py smoke --pg "postgresql://postgres:teste@localhost:5433/postgres"
```

Este é **o** comando. Ele faz o pipeline inteiro e confere cada etapa:

1. aplica `otimizador/infraestrutura/sql/ddl_input.sql` e `otimizador/infraestrutura/sql/ddl_resultado.sql` — 16 tabelas de entrada, 14 de saída, 3 views;
2. carrega uma fixture nas tabelas de `input` — é aqui que as PKs e FKs encostam em dado real;
3. insere uma `controle.run_request`;
4. roda `job_databricks.rodar()` fim a fim;
5. confere no banco: VPL do cabeçalho igual à soma por sub-bacia, CAPEX reconciliando entre
   ano/mês/cidade, frações de rateio somando 1, teto respeitado;
6. **roda a mesma `run_id` de novo** e compara as contagens das 13 tabelas de detalhe
   (as 14 publicadas menos o cabeçalho, conferido à parte).

O passo 6 é o que só o banco prova: republicar tem de **apagar e regravar**, não acumular.

Ele cria schemas próprios (`smoke_*`) e os derruba no fim. Para rodar contra os nomes de
produção, `--schemas-reais`; para inspecionar depois, `--manter`.

### B.3 Os testes de integração

```bash
# bash
export OTIMIZADOR_PG_TESTE="postgresql://postgres:teste@localhost:5433/postgres"
# PowerShell
$env:OTIMIZADOR_PG_TESTE = "postgresql://postgres:teste@localhost:5433/postgres"

python -m pytest tests/test_publicacao_postgres.py -v
```

Os 12 que estavam pulando agora rodam: idempotência, atomicidade da transação,
`ON DELETE CASCADE`, upsert de status e o `CHECK` dos estados.

> ⚠️ **Estes testes nunca foram executados** — foram escritos contra a implementação, numa
> máquina sem Postgres. É esperado que a primeira execução peça ajustes. Se algum falhar,
> leia a mensagem antes de assumir que o código está errado: pode ser o teste.

### B.4 Olhar o resultado no banco

```bash
python main.py smoke --pg "..." --manter
psql "postgresql://postgres:teste@localhost:5433/postgres"
```

```sql
SELECT run_id, status FROM smoke_controle.run_status;
SELECT * FROM smoke_public.otim_vw_historico;
SELECT obra_id, cidade, capex, construida, status, motivo
  FROM smoke_public.otim_obra WHERE run_id = 'smoke_0001' LIMIT 20;
```

As consultas de validação estão em [`06-dicionario-resultado.md`](06-dicionario-resultado.md)
§6.11, e o contrato de leitura por tela em
[`02-integracao-backend.md`](02-integracao-backend.md) §2.5.

---

## Nível C — o job de produção, na sua máquina

O smoke test já chama `rodar()`, mas vale fazer uma vez à mão para ver que **não há nada de
Databricks ali**. Com o banco do nível B de pé e o schema criado:

```python
import json, psycopg2
from otimizador.aplicacao.job_databricks import rodar

PG = "postgresql://postgres:teste@localhost:5433/postgres"

# 1. o backend faria isto: registrar o pedido
params = {"ORCAMENTO": {"2026": 3_000_000, "2027": 3_000_000},
          "USAR_CTS": True, "USUARIO": "eu@local"}
with psycopg2.connect(PG) as conn, conn.cursor() as cur:
    cur.execute("""INSERT INTO controle.run_request (run_id, params) VALUES (%s, %s::jsonb)
                   ON CONFLICT (run_id) DO UPDATE SET params = EXCLUDED.params;""",
                ("local_001", json.dumps(params)))

# 2. o Databricks faria isto: executar o job
print(rodar("local_001", PG))

# 3. rodar de novo é seguro — apaga e regrava
print(rodar("local_001", PG))
```

No Databricks a única diferença são as três primeiras linhas do notebook:

```python
rodar(run_id=dbutils.widgets.get("run_id"),
      pg_url=dbutils.secrets.get("otimizador", "pg_url"),
      blob=dbutils.widgets.get("blob_uri"),
      service_bus=dbutils.secrets.get("otimizador", "sb_conn"))
```

`dbutils` só serve para buscar o segredo e o widget. Tudo o mais é idêntico.

---

## 7.4 Erros comuns nesta fase

| Sintoma | Causa | Solução |
|---|---|---|
| `ModuleNotFoundError: No module named 'otimizador'` | rodou de fora da raiz do projeto | rode da pasta com o `main.py` e o `pytest.ini` |
| `ModuleNotFoundError: matplotlib` | dependência de `dashboard_otimizador_v2` | está no requirements; `pip install matplotlib` |
| Testes de solver pulando | `ortools` ausente | `pip install ortools` |
| `UnicodeEncodeError` no console | terminal do Windows em cp1252 | `chcp 65001` ou use o Windows Terminal |
| `could not connect to server` | container não subiu, ou porta errada | `docker ps`; a porta local aqui é a **5433** |
| `ValueError: run_request.params com chaves desconhecidas` | typo ou caixa errada na chave | as aceitas estão em `job_databricks.MAPA_PARAMS` |
| `sem teto anual de CAPEX` | faltou `ORCAMENTO` | mande `ORCAMENTO`; `ORCAMENTO_TOTAL` sozinho não serve |
| Comparações todas iguais | orçamento não restringe | baixe `--orcamento` (§A.4) |

---

## 7.5 O que você **não** consegue testar sem Databricks

Para não haver ilusão de cobertura:

- **Conectividade cluster → Postgres na Azure** (Private Endpoint, firewall).
- **Secret Scope** — localmente a URL vem de parâmetro; lá vem de `dbutils.secrets`.
- **Escrita no ADLS** (`blob=`) — precisa de credencial de storage e da configuração do
  cluster. Localmente dá para passar um caminho de pasta e ver o parquet sendo gravado, o
  que exercita o código, mas não o storage remoto.
- **Notificação por Service Bus** — precisa da connection string.
- **Comportamento sob o Runtime** — versões de pandas/numpy do DBR podem diferir das suas.

Tudo o que é **cálculo, contrato de dados e transação** você consegue provar localmente. O
que fica para o Databricks é infraestrutura.

---

## 7.6 Ordem sugerida para os primeiros dias

1. Suíte verde (§7.0) e uma rodada do nível A — entender o que o otimizador decide.
2. `--comparar orcamento` e `--comparar cts` — entender **por que** ele decide assim.
3. Ler [`01-visao-geral.md`](01-visao-geral.md), em especial §1.8 (conceitos de domínio): a
   cadeia até a ETE, o rateio por vazão e a régua de cobertura explicam a maior parte dos
   resultados que parecem estranhos.
4. Nível B — smoke test e os 12 testes de integração. **Reporte o que quebrar**: são os
   primeiros a rodar contra um banco.
5. Ler [`REVISAO_PRODUCAO.md`](../REVISAO_PRODUCAO.md) — os achados e o que ficou pendente.
6. Nível C e, só então, Databricks.

---

Voltar ao [índice](README.md).
