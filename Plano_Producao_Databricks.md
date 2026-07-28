> ⚠️ **DOCUMENTO HISTÓRICO — superado pela implementação.**
> Este é o plano original, mantido como registro das decisões de arquitetura (§1 e §4 seguem
> válidos e valem a leitura). Os números e estados **estão desatualizados**: fala em "26
> testes" (são 82), "13 tabelas `run_*`" (são 14), Fase 4 como "esqueleto" com TODOs que já
> foram feitos, e caminhos `producao/*` que não existem — o pacote é flat.
>
> Para o estado atual: [`docs/README.md`](docs/README.md) (documentação por assunto) e
> [`REVISAO_PRODUCAO.md`](REVISAO_PRODUCAO.md) (o que foi revisado, corrigido e o que
> continua sem prova de execução). O checklist de revisão do §6 aqui foi respondido item a
> item na §3 da REVISAO.

# Otimizador de CAPEX — Produção no Databricks (Azure) com Postgres

Plano em fases para levar o otimizador de esgoto a um **job no Databricks** que lê o input de
um **Postgres na Azure** e grava o resultado no mesmo Postgres, com **portão de qualidade** antes
de publicar e um desenho **fácil de manter por engenheiro humano**.

> Documento para revisão (Claude Code + Codex). Cada fase lista o que muda, os arquivos, as
> decisões e um checklist de revisão. No fim há a lista de TODOs que dependem de Postgres/backend.

---

## 1. Princípio de arquitetura (a decisão mais importante)

O **motor** de otimização fica **puro** — só objetos em memória, zero I/O. É isso que mantém os
26 testes possíveis e a manutenção sã. A leitura/escrita no Postgres é um **adaptador** ao redor
do motor, em módulo próprio.

```
                 ┌─────────────────────────────────────────────┐
   Postgres      │  PACOTE DO OTIMIZADOR (job Databricks)       │      Postgres
  (input/ctrl) ─▶│  dados/  ─▶  engine (PURO)  ─▶  qualidade    │─▶  (resultado run_*)
                 │     ▲            ▲                 │          │
                 │  carregar_    ler_banco        checar()      │
                 │  postgres     (Excel, p/ dev)                │
                 └─────────────────────────────────────────────┘
```

O job é dono do I/O (o backend **não** empurra/puxa linhas por API — só dispara e depois lê o
resultado). Mas dentro do pacote a fronteira **motor ≠ dados** é rígida.

### Fluxo de uma rodada

```
Front (cadastro) ──escreve──▶ Postgres schema `input`  (as 15 tabelas do cadastro)
Backend ──insere──▶ Postgres `controle.run_request`  (1 linha = parâmetros da célula PARAMETROS)
Backend ──dispara──▶ Databricks Job (Jobs API ou fila Service Bus)
                       1. lê run_request + input do Postgres
                       2. monta Cenário → resolve (OR-Tools)
                       3. materializa em tabelas run_*
                       4. PORTÃO DE QUALIDADE ── falhou? ─▶ FALHOU_QUALIDADE, grava diagnóstico, NÃO publica
                       5. passou? ─▶ publica run_* (transacional) + SUCESSO
Front/Backend ──lê──▶ Postgres `run_*`  (o contrato que o leitor_v2 já consome)
```

---

## 2. O que já existe (reaproveitar) × o que é novo

| Peça | Situação |
|---|---|
| Motor (`otimizador_capex_v62.py`) + solver (`cpsat63`) | **existe** — puro, testado |
| Materialização em tabelas (`persistencia.py`) | **existe** — 14 tabelas `run_*` + snapshots |
| DDL das tabelas de RESULTADO (`publicacao.ddl_postgres`) | **existe** |
| Escrita idempotente por `run_id` + `marcar_status` (`publicacao.publicar`) | **existe** — FK cascade |
| Suíte de regressão (26 testes pytest) | **existe** — porta de CI |
| **Adaptador de leitura Postgres → Cenário** | **novo** — `producao/carregar_postgres.py` |
| **Portão de qualidade por rodada** | **novo** — `producao/qualidade.py` |
| **Orquestração do job** | **novo** — `producao/job_databricks.py` |
| **DDL das tabelas de INPUT + CONTROLE** | **novo** — `producao/ddl_input.sql` |
| `run_request` / `run_status` / `run_diagnostico` | **novo** — schema `controle` |

Ou seja: **todo o caminho de escrita já existe**; o novo é o **lado da leitura**, a **orquestração**
e o **portão de qualidade**.

---

## 3. Fases

### Fase 1 — Modelo de dados no Postgres  ✅ (DDL gerado)

- **Entrega:** `producao/ddl_input.sql` — cria `schema input` (15 tabelas de cadastro, geradas a
  partir do esquema real) e `schema controle` (`run_request`, `run_status`, `run_diagnostico`).
- As tabelas de **resultado** (`run_*`) continuam vindo de `publicacao.ddl_postgres(tabs)`.
- **Decisão:** input, controle e resultado em **schemas separados** (`input`, `controle`, `public`)
  para permissões distintas (o front escreve `input`; o job escreve `public`/`controle`).

**Revisar:** tipos das colunas (o gerador inferiu `text`/`integer`/`double precision` por amostra);
chaves primárias e índices que o backend vai precisar; se `input` deve ter FKs entre as tabelas.

### Fase 2 — Adaptador Postgres → Cenário  ✅ (esqueleto + mecanismo validado)

- **Entrega:** `producao/carregar_postgres.py`. Lê as tabelas de `input`, materializa num `.xlsx`
  temporário com os **mesmos nomes de aba/coluna** e chama `ler_banco`. Assim o comportamento é
  **idêntico** ao caminho Excel (derivação de `novas`, CTS, industrial, cobertura por unidade,
  avisos) — **zero lógica duplicada**.
- **Validado offline:** o round-trip `xlsx → DataFrames → xlsx → ler_banco` dá Cenário **idêntico**
  (mesmos nós/obras/VPL/vazão). Prova que a materialização não altera nada.
- **Evolução limpa (Fase 2b, opcional):** trocar `ler_banco(path)` por `ler_banco(fonte)` aceitando
  um dict de DataFrames, eliminando o arquivo temporário. Fica para depois de validar em produção.

**Revisar:** o mapa `ABAS_INPUT` (aba do motor → tabela física); tratamento de abas opcionais (CTS);
se o `.xlsx` temporário é aceitável no cluster (é — DBFS/local) ou se já parte para a Fase 2b.

### Fase 3 — Portão de qualidade por rodada  ✅ (implementado + testado)

- **Entrega:** `producao/qualidade.py` → `checar(cen, res, tabs)` devolve `(ok, relatório, resumo)`.
- Formaliza os invariantes que o notebook já calcula: reconciliações VPL/CAPEX = **zero**, frações
  de rateio somam 1, teto de orçamento respeitado, sem `NaN`, metas com déficit não-negativo,
  cobertura não-negativa, status do solver OPTIMAL/FEASIBLE.
- **Testado:** roda sobre uma materialização real; pegou corretamente um plano que estoura o teto
  (build-all) e passou todas as reconciliações. Com o plano do solver, passa.
- **Dois portões (não um):** este é o portão **por rodada** (qualidade do resultado); o portão de
  **código** é a suíte pytest (CI, antes do deploy). São coisas distintas.

**Revisar:** o conjunto de checagens críticas × avisos; a tolerância (R$ 0,01); se falta alguma
regra de negócio específica do cliente para barrar publicação.

### Fase 4 — Orquestração do job  ✅ (esqueleto)

- **Entrega:** `producao/job_databricks.py` → `rodar(run_id, pg_url, ...)`. Entrypoint **fino**:
  lê `run_request` → `RODANDO` → `carregar_postgres` → `resolver` → `materializar` → **portão** →
  publica + `SUCESSO` ou grava diagnóstico + `FALHOU_QUALIDADE`; qualquer erro técnico → `ERRO`.
- Idempotente por `run_id` (reprocessar apaga e regrava), retriável.
- **Conexão via Databricks Secret Scope**, nunca hardcoded.

**Revisar / TODO:** `publicacao.gravar_diagnostico(...)` (novo — grava a lista em
`controle.run_diagnostico`); embrulhar `publicar` numa **transação** única (tudo-ou-nada); a
tradução `run_request.params → kwargs` (`_params_para_ler_banco`).

### Fase 5 — CI, empacotamento e runbook  ⬜ (a fazer)

- **Empacotar como wheel** com módulos claros: `engine` · `dados` · `qualidade` · `persistencia` ·
  `publicacao` · `job`. Testes ao lado. O job do Databricks vira um entrypoint do wheel (ou 1
  célula que importa o wheel), não um notebook gigante.
- **CI** (Azure Pipelines/GitHub Actions): roda `pytest` a cada merge; publica o wheel.
- **Runbook** para o operador: como disparar, ler status (`SELECT * FROM controle.run_status`),
  o que significa cada status, como reprocessar (é só reprocessar o `run_id`).

---

## 4. Como disparar o job (decisão de integração)

Recomendação: o backend insere o `run_request` e aciona via **Databricks Jobs API** *ou* publica
numa fila **Service Bus** que o job consome. A fila **desacopla** o backend da disponibilidade do
cluster: se o cluster estiver ocupado/reiniciando, a mensagem espera na fila. `publicar` já sabe
notificar por Service Bus/webhook ao concluir.

---

## 5. Segurança e manutenção (para o engenheiro humano)

- **Credenciais só em Secret Scope** (`pg_url`, Service Bus). Nada no código nem em widget.
- **Permissões por schema:** front escreve `input`; job escreve `public`/`controle`; leitura do
  front só em `public`.
- **Status observável:** `controle.run_status` dá o estado de cada rodada num `SELECT`.
- **Idempotência:** reprocessar um `run_id` é seguro (apaga e regrava).
- **Caminho Excel preservado** para dev/testes locais — o motor nunca sabe a origem.

---

## 6. Checklist de revisão (para Claude Code + Codex)

- [ ] **Fronteira motor ≠ dados** mantida? (nenhum SQL dentro de `otimizador_capex_*` / `cpsat*`)
- [ ] `ABAS_INPUT` cobre todas as abas que `ler_banco` lê? nomes de tabela coerentes com o DDL?
- [ ] DDL de input: tipos corretos, PKs/índices, FKs necessárias? schemas e permissões?
- [ ] Portão de qualidade: falta alguma checagem crítica? tolerância adequada? críticos × avisos?
- [ ] Transação na publicação (tudo-ou-nada) implementada?
- [ ] `publicacao.gravar_diagnostico` criado e testado?
- [ ] Secrets: nenhuma credencial no código; conexão vem do Secret Scope?
- [ ] Retry/idempotência: reprocessar `run_id` é seguro em todos os caminhos?
- [ ] Fase 2b (dict de DataFrames) vale a pena já, ou o `.xlsx` temporário basta por ora?
- [ ] Empacotamento (wheel) e CI cobrem os 26 testes antes de publicar o wheel?

---

## 7. Arquivos desta entrega

| Arquivo | Fase | Estado |
|---|---|---|
| `producao/ddl_input.sql` | 1 | gerado (revisar tipos/índices) |
| `producao/carregar_postgres.py` | 2 | esqueleto + mecanismo validado offline |
| `producao/qualidade.py` | 3 | implementado + testado |
| `producao/job_databricks.py` | 4 | esqueleto (TODOs marcados no código) |
| `producao/Plano_Producao_Databricks.md` | — | este documento |

Dependências novas em produção: `sqlalchemy` + `psycopg2` (Postgres), `azure-servicebus`
(notificação) — todas já previstas no `publicacao.py`.
