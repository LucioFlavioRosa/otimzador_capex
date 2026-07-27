# Documentação — Otimizador de CAPEX em produção

Sete documentos, um por assunto. Cada um é autocontido; a ordem abaixo é a de leitura para
quem chega agora.

| # | Documento | Para quem | Responde |
|---|---|---|---|
| 1 | [Visão geral da solução](01-visao-geral.md) | todos | o que o sistema faz, por que a arquitetura é assim, mapa dos módulos, conceitos de domínio |
| 2 | [Integração com o backend](02-integracao-backend.md) | backend + front | o que escrever, como disparar, contrato completo dos parâmetros, o que ler em cada tela |
| 3 | [Colocar em produção](03-producao.md) | plataforma / dados | secrets, DDL, permissões, entrypoint, operação, runbook de falhas |
| 4 | [Execução dos testes](04-testes-executar.md) | todos os devs | como rodar, o que os skips significam, CI, golden |
| 5 | [O que os testes cobrem](05-testes-cobertura.md) | quem vai mudar código | o que já está protegido, o portão de qualidade, e as lacunas |
| 6 | [Dicionário do schema de saída](06-dicionario-resultado.md) | backend + front | as 258 colunas de `public.otim_*`, coluna a coluna, e as consultas de validação |
| 7 | [Rodar tudo sem Databricks](07-rodar-local.md) | **quem está começando** | do clone à execução do job de produção na própria máquina, em três níveis |

## Trilhas por papel

**Dev de backend** → 1 (§1.4 fluxo, §1.5 estados, §1.8 domínio) → **2 inteiro** → **6** (as
colunas que você vai ler) → 3 §3.6–3.7.

**Plataforma / DevOps** → 1 (§1.3, §1.7) → **3 inteiro** → 4 §4.5 (CI).

**Quem vai manter o otimizador** → **1 inteiro** → 5 → 4 → 2 §2.2 (o cadastro).

**Quem só precisa rodar os testes** → 4.

**Quem ainda não tem acesso ao Databricks** → **7 inteiro**, depois 1 e 5. O nível A do
documento 7 roda em minutos e só precisa de Python.

## Documentos irmãos, fora desta pasta

- [`../README_producao.md`](../README_producao.md) — runbook curto, uma página.
- [`../Plano_Producao_Databricks.md`](../Plano_Producao_Databricks.md) — o plano em fases,
  com as decisões de arquitetura e o checklist de revisão.
- [`../REVISAO_PRODUCAO.md`](../REVISAO_PRODUCAO.md) — revisão de código: achados por
  severidade, os diffs aplicados e o que **continua sem verificação**. Leia antes de mexer em
  `publicacao.py`, `job_databricks.py` ou no DDL.
- [`../tests/README.md`](../tests/README.md) — detalhe das fixtures e dos testes do motor.

## Subindo o banco pela primeira vez

```bash
python smoke_test_postgres.py --pg "postgresql://..."
```

Pipeline inteiro contra o Postgres, incluindo a republicação do mesmo `run_id`. Detalhes em
[04](04-testes-executar.md) §4.4; consultas de validação manual em
[06](06-dicionario-resultado.md) §6.11.

## O mínimo absoluto, se você só tem 5 minutos

1. O motor é **puro** — nada de I/O em `otimizador_capex_*.py`. Leitura em
   `carregar_postgres.py`, escrita em `publicacao.py`.
2. O **`run_id` vem do backend** e amarra `controle.run_request` → `controle.run_status` →
   `public.otim_meta`.
3. **Reprocessar o mesmo `run_id` é seguro**: apaga e regrava, numa transação só.
4. **Chave desconhecida em `params` é erro**; chave ausente usa o default do motor, nunca um
   default do job.
5. `pytest tests/` → **61 passed, 13 skipped**. Os 13 skips são esperados (12 precisam de
   Postgres, 1 precisa da suíte legada).
