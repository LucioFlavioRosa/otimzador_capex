# 4. Execução dos testes

Público: qualquer dev que vá mexer no pacote, e quem montar o CI.

**Regra:** rode a suíte **antes e depois** de qualquer mudança. Não altere a semântica de um
teste nem um valor golden para "fazer passar" — se o golden mudou, ou você achou um bug, ou
mudou o comportamento de propósito (e aí é uma decisão consciente, ver §4.6).

---

## 4.1 Setup

Da pasta do pacote (onde estão os `.py` e o `pytest.ini`):

```bash
python -m pip install -r requirements-prod.txt
python -m pytest -q tests/
```

Python 3.10+. Instalação limpa leva ~2 min (o `ortools` é o pesado); a suíte roda em ~2 s.

Se você não vai rodar o pipeline, o mínimo para a suíte é:
`pytest`, `pandas`, `openpyxl`, `ortools` (só os testes de solver), `matplotlib` (só um teste).

---

## 4.2 Resultado esperado

```
$ python -m pytest tests/
79 collected
66 passed, 13 skipped in 2.9s
```

| Arquivo | Testes |
|---|---|
| `test_nucleo.py` | 9 |
| `test_cts.py` | 9 |
| `test_classe.py` | 7 |
| `test_derivadas.py` | 2 |
| `test_regressao_golden.py` | 4 |
| `test_producao.py` | 36 |
| `test_publicacao_postgres.py` | 12 |

**Os 13 skips são esperados**, não são falha:

| Quantos | Quem | Por quê | Some se… |
|---|---|---|---|
| 12 | `test_publicacao_postgres.py` | precisa de um Postgres | definir `OTIMIZADOR_PG_TESTE` (§4.4) |
| 1 | `test_nucleo.py::test_separabilidade_por_cidade_e_exata` | importa `testes_otimizador`, a suíte legada que **não acompanha** este pacote | copiar `testes_otimizador.py` para a pasta |

**Sem OR-Tools o total sobe para 21 skips** — nenhuma falha. São 5 testes marcados `solver`
(um deles, a separabilidade, já está entre os 13 acima) mais os 4 de `rodar()` fim a fim,
que chamam o CP-SAT de verdade e por isso pulam junto. Sem matplotlib, mais 1.

> Se algum desses testes **falhar** em vez de pular num ambiente sem OR-Tools, é bug —
> foi o que aconteceu quando os testes de `rodar()` foram escritos sem o
> `importorskip("ortools")` na fixture, e faria o CI offline nascer vermelho.
**Nenhum skip é aceitável em vermelho:** se um teste *falhar* em vez de pular, é bug.

---

## 4.3 Comandos úteis

```bash
python -m pytest -q tests/                    # tudo
python -m pytest tests/ -v                    # com o nome de cada teste
python -m pytest -m "not solver" tests/       # sem OR-Tools (rápido)
python -m pytest -m solver tests/             # só os do solver
python -m pytest -m "not slow" tests/         # pula a separabilidade por cidade
python -m pytest tests/test_producao.py -v    # só a camada de produção
python -m pytest tests/ -k industrial         # por nome
python -m pytest tests/ -x --ff               # para no 1º erro, começando pelos que falharam
python -m pytest tests/ -rs                   # mostra o motivo de cada skip
```

Marcadores (`pytest.ini`): `solver` (precisa de OR-Tools) e `slow` (decomposição por cidade).

---

## 4.4 Os testes que precisam de Postgres

`tests/test_publicacao_postgres.py` cobre o que **não dá para provar offline**: idempotência da
republicação, atomicidade da transação, `ON DELETE CASCADE`, upsert de status e o `CHECK` dos
estados. Sem a variável de ambiente, o módulo inteiro pula.

### Com Docker

```bash
docker run --rm -d -p 5433:5432 -e POSTGRES_PASSWORD=teste --name pg-otim postgres:16
```

```bash
# bash
export OTIMIZADOR_PG_TESTE="postgresql://postgres:teste@localhost:5433/postgres"
```

```powershell
# PowerShell
$env:OTIMIZADOR_PG_TESTE = "postgresql://postgres:teste@localhost:5433/postgres"
```

```bash
python -m pytest tests/test_publicacao_postgres.py -v
docker rm -f pg-otim
```

### O smoke test (rode este primeiro)

`smoke_test_postgres.py` faz o pipeline inteiro contra um Postgres: aplica os dois DDL,
carrega uma fixture nas tabelas de `input`, insere uma `run_request`, roda o job fim a fim,
confere as reconciliações **no banco** e **roda a mesma `run_id` de novo** para provar a
idempotência. É o teste que a suíte não consegue fazer.

```bash
python smoke_test_postgres.py --pg "postgresql://postgres:teste@localhost:5433/postgres"
```

Por padrão cria `smoke_input` / `smoke_controle` / `smoke_public` e derruba no fim.
`--schemas-reais` roda contra `input`/`controle`/`public` (mais fiel, use em banco
descartável); `--manter` deixa os schemas de pé para você inspecionar; `--forcar` recria
schemas que já existam. Sai com código 1 se qualquer verificação falhar — serve de gate.

### Com um Postgres local

Qualquer instância serve — inclusive a de desenvolvimento. Só aponte a variável para ela.

**Isolamento:** os testes criam os schemas `otim_teste_pub` e `otim_teste_ctrl` e os derrubam
no fim (`DROP SCHEMA ... CASCADE`). Não encostam em `public`, `input` nem `controle`. Ainda
assim, prefira um banco descartável.

**Detalhe de manutenção:** o DDL de controle usado pelo teste é **extraído do próprio
`ddl_input.sql`**, trocando só o nome do schema. Se você mudar o DDL — coluna nova, `CHECK`
novo — o teste passa a exercitar a versão nova sem ninguém precisar editá-lo.

> ⚠️ **Estes 12 testes nunca foram executados.** Foram escritos contra a implementação, mas na
> máquina onde o pacote foi revisado não havia Postgres. Espere ajustes na primeira execução —
> é a natureza de um teste de integração que ainda não viu o banco. O que foi verificado
> offline: o módulo coleta e o `_ddl_controle` extrai o SQL corretamente.

---

## 4.5 CI

O gate de código é a suíte inteira. Sugestão para GitHub Actions:

```yaml
name: testes
on: [push, pull_request]

jobs:
  offline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install -r requirements-prod.txt
      - run: pytest -q tests/          # 66 passed, 13 skipped

  com-postgres:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: {POSTGRES_PASSWORD: teste}
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready --health-interval 5s
          --health-timeout 5s --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install -r requirements-prod.txt
      - run: pytest -q tests/test_publicacao_postgres.py -v
        env:
          OTIMIZADOR_PG_TESTE: postgresql://postgres:teste@localhost:5432/postgres
```

O job `offline` é rápido (~2 min com a instalação) e deve bloquear qualquer merge. O
`com-postgres` é o que dá confiança para publicar o wheel.

---

## 4.6 Atualizar o golden

`test_regressao_golden.py` trava VPL, CAPEX, cobertura, universo, vazão e número de obras do
**build-all** (determinístico — independe do orçamento), nos modos CTS ligado e desligado.

Se você mudou o comportamento **de propósito** e o golden acusou:

```bash
python tests/atualiza_golden.py
```

Ele imprime o bloco novo. **Revise número por número antes de colar** — cada valor que mudou
precisa ter explicação. Golden atualizado sem justificativa é a forma mais eficiente de
esconder uma regressão.

O teste do **solver** não trava número fixo (variaria entre versões do OR-Tools): checa o
invariante `VPL(solver) ≥ VPL(build-all)` — o build-all é o piso "constrói tudo", e o solver
nunca pode ficar pior, porque larga obra que destrói valor.

---

## 4.7 Fixtures

A suíte é **autossuficiente**: lê só de `tests/fixtures/`, sem depender dos bancos regionais.

| Fixture | O que tem | Alimenta |
|---|---|---|
| `banco_teste_CTS_poc_v2.xlsx` | banco pequeno **com CTS** (2 coletores) | testes de CTS, golden, produção |
| `banco_fixture_testes.xlsx` | **sem CTS**, com **mix de WACC** (~60% vazios que herdam o `wacc_medio`) | retrocompatibilidade e regra de WACC |
| `banco_fixture_classe.xlsx` | **parcela industrial** em b1/b3; cidade c1 medindo em economias, c2 em população | classe residencial/industrial e régua de cobertura |

Rodam em qualquer sessão (local, CI, Colab) sem banco externo.

---

## 4.8 Problemas comuns

| Sintoma | Causa | Solução |
|---|---|---|
| `ModuleNotFoundError: otimizador_capex_v62` | rodou de fora da pasta do pacote | rode da pasta que tem o `pytest.ini`; `tests/_helpers.py` insere a raiz no `sys.path` |
| `ModuleNotFoundError: matplotlib` | dependência do `dashboard_otimizador_v2` | `pip install matplotlib` (já está no `requirements-prod.txt`) |
| 4 testes pulando com "OR-Tools indisponivel" | `ortools` ausente | `pip install ortools` |
| 12 pulando com "defina OTIMIZADOR_PG_TESTE" | comportamento esperado | §4.4 |
| 1 pulando com "suite legada ausente" | comportamento esperado | §4.2 |
| Golden falhou sem você mexer no motor | versão de pandas/numpy mudou o arredondamento | investigue **antes** de atualizar o golden |
| Suíte lenta (> 30 s) | os testes de solver com cenário grande | `-m "not slow"` durante o desenvolvimento |

---

Próximo: **`05-testes-cobertura.md`** (o que cada teste protege).
