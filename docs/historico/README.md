# Histórico

Documentos que registram **decisões e revisões de um momento**, não o estado atual do pacote.

Eles ficam aqui porque apagar perderia trilha de auditoria — e porque deixá-los ao lado dos
documentos operacionais faz alguém seguir instrução vencida. Nada aqui é contrato.

| Documento | O que é | Por que não vale como fonte |
|---|---|---|
| `REVISAO_PRODUCAO.md` | relatório de uma revisão de código do pacote de produção, com achados e diffs sugeridos | cita `arquivo.py:linha` do código daquele momento e traz contagens de suíte daquela época |
| `Plano_Producao_Databricks.md` | plano original de arquitetura (Databricks + Postgres) | fala em caminhos `producao/*` que não existem e em fases que já foram implementadas |

**Onde está a fonte atual:** [`../README.md`](../README.md) indexa os sete documentos que
descrevem o pacote como ele é hoje. Contrato com o backend em
[`../02-integracao-backend.md`](../02-integracao-backend.md); o que os testes garantem, em
[`../05-testes-cobertura.md`](../05-testes-cobertura.md).

As duas decisões de arquitetura do plano que **continuam valendo** — o motor puro, sem I/O, e o
adaptador que traduz Postgres para o `Cenario` — estão descritas em
[`../01-visao-geral.md`](../01-visao-geral.md), que é onde devem ser lidas.
