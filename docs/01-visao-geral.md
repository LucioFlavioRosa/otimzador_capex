# 1. Visão geral da solução

Público: qualquer pessoa que vá mexer neste pacote. Leia antes dos outros quatro documentos.

---

## 1.1 O que o sistema resolve

Dado o cadastro de uma **unidade regional** de saneamento — sub-bacias de esgoto, obras
possíveis em cada uma, ETEs, metas de cobertura por cidade e o teto de CAPEX por ano — o
otimizador decide **quais obras fazer e em que mês começar cada uma**, maximizando o VPL da
carteira sujeito a:

- **teto de CAPEX** por ano (e, opcionalmente, um teto total da janela);
- **metas de cobertura** por cidade e ano (com penalidade configurável);
- **dependências físicas**: uma ligação só fatura se toda a cadeia até a ETE existir;
- **capacidade da ETE**: vazão conectada não pode passar da capacidade instalada;
- **janelas**: obras obrigatórias em certo ano, obras proibidas até certo ano;
- **fim de concessão** por cidade, que define o horizonte de cada sistema.

A saída não é só o plano: é o plano **explicado**. Para cada obra que ficou de fora há uma
categoria de motivo e o elo que travou; para cada sub-bacia, o VPL decomposto em receita
direta, indireta, CAPEX rateado e OPEX.

---

## 1.2 O princípio de arquitetura (a decisão que sustenta todo o resto)

> **O motor é puro. Zero I/O.** `otimizador_capex_v62.py` e `otimizador_capex_cpsat63.py`
> não abrem arquivo, não falam SQL, não fazem rede. Só transformam objetos em memória.

Toda a leitura e escrita vive em **adaptadores** ao redor:

```
                 ┌──────────────────────────────────────────────────┐
   Postgres      │   PACOTE DO OTIMIZADOR  (job no Databricks)      │      Postgres
  input/controle │                                                  │   public.otim_*
       │         │   carregar_postgres ─▶ MOTOR (PURO) ─▶ qualidade │         ▲
       └────────▶│           ▲              │                  │    │─────────┘
                 │      ler_banco      cpsat63 (solver)   persistencia
                 │      (Excel, dev)        │                  │    │
                 └──────────────────────────┼──────────────────┼────┘
                                            └── publicacao ────┘
```

Consequências práticas — é por isso que vale a pena manter:

1. **A suíte de testes existe.** 66 dos 79 testes rodam sem banco, sem rede e sem credencial,
   em ~2 s (os outros 13 pulam: 12 precisam de Postgres, 1 precisa da suíte legada). Se o
   motor tivesse SQL dentro, nada disso seria testável.
2. **O caminho Excel continua funcionando.** `ler_banco(<arquivo.xlsx>)` é o caminho de
   desenvolvimento e das fixtures. O motor não sabe se os dados vieram de Excel ou do
   Postgres — e é isso que garante que o job em produção calcule **exatamente** o mesmo que
   o notebook do analista.
3. **Trocar a origem dos dados não toca no cálculo.** A Fase 2b (ler direto de DataFrames,
   sem o `.xlsx` temporário) muda um adaptador, não o motor.

**Regra para o time:** um `import psycopg2`, um `open()` ou um `requests` dentro de
`otimizador_capex_*.py` é um bug de arquitetura, mesmo que funcione.

---

## 1.3 Mapa dos módulos

| Camada | Arquivo | Papel | Pode ter I/O? |
|---|---|---|---|
| **Motor** | `otimizador_capex_v62.py` | modelo econômico, `ler_banco`, `avaliar`, VPL por sub-bacia | **Não** (exceto o `openpyxl` do próprio `ler_banco`) |
| **Solver** | `otimizador_capex_cpsat63.py` | OR-Tools CP-SAT, geração de colunas por cidade | **Não** |
| **Apoio** | `dashboard_otimizador_v2.py` | explicabilidade usada pela persistência | matplotlib |
| **Materialização** | `persistencia.py` | `cen + res` → 14 tabelas `run_*` | escreve parquet/Delta |
| **Publicação** | `publicacao.py` | DDL de resultado, escrita transacional, status, diagnóstico, notificação | **Sim** — é o dono da escrita |
| **Leitura (input)** | `carregar_postgres.py` | Postgres `input` → `Cenário` | **Sim** — é o dono da leitura |
| **Qualidade** | `qualidade.py` | portão por rodada, antes de publicar | Não |
| **Orquestração** | `job_databricks.py` | entrypoint fino: amarra tudo e trata erro | via os adaptadores |
| **Contrato de leitura** | `leitor_v2.py` | reconstrói as telas **só** a partir das tabelas | não importa o motor |
| **DDL** | `ddl_input.sql`, `ddl_input_migracao_01.sql` | schemas `input` e `controle` | — |

`leitor_v2.py` merece atenção: ele **não importa** o motor, o solver nem o dashboard — só lê
DataFrames. Se as telas se reconstroem com ele, o contrato de dados está completo e o backend
consegue montar as mesmas telas lendo o Postgres. É a prova viva do contrato.

---

## 1.4 O fluxo de uma rodada

```
Front (cadastro)  ──escreve──▶  Postgres schema `input`   (16 tabelas do cadastro)
Backend           ──insere──▶  `controle.run_request`     (1 linha = os parâmetros da rodada)
Backend           ──dispara─▶  Databricks Job (Jobs API ou fila Service Bus)
                                  │
                                  ▼   job_databricks.rodar(run_id, pg_url, ...)
                        1. marca RODANDO em controle.run_status
                        2. lê controle.run_request                    → params
                        3. carrega e exige teto anual de CAPEX (ver 3b abaixo)
                        4. carregar_postgres(input)                   → Cenário
                        5. cpsat63.resolver_por_sistema(cen)          → plano ótimo
                        6. persistencia.materializar(cen, res, run_id=run_id) → 14 tabelas
                        7. qualidade.checar(cen, res, tabs)           → PORTÃO
                             ├─ reprovou → grava diagnóstico, FALHOU_QUALIDADE, NÃO publica
                             └─ passou   → 8
                        8. publicacao.publicar(...): blob (se configurado)
                           → UM commit: public.otim_* + run_status = SUCESSO
                        9. notifica (Service Bus / webhook) — sempre DEPOIS do commit
                                  │
Front/Backend     ──lê──────▶  Postgres `public.otim_*` e as views
```

Dois detalhes que economizam horas de depuração:

- **O `run_id` é do backend**, não do job. Ele é gerado por quem insere a `run_request` e
  atravessa tudo: `controle.run_status`, `controle.run_diagnostico` e `public.otim_meta`.
  É a única chave que liga os três schemas.
- **O passo 8 é uma transação só.** Os dados publicados e o `SUCESSO` entram juntos ou não
  entram. O estado observável nunca mente sobre o que está no banco.

---

## 1.5 Estados de uma rodada

`controle.run_status.status` — o `CHECK` do DDL só aceita estes cinco:

| Estado | Quem escreve | Significa |
|---|---|---|
| `PENDENTE` | backend (opcional) | requisição criada, job ainda não pegou |
| `RODANDO` | job, no início | está processando |
| `SUCESSO` | job, no commit da publicação | resultado publicado em `public.otim_*` |
| `FALHOU_QUALIDADE` | job | rodou até o fim, mas o resultado **reprovou** no portão. **Nada foi publicado.** O porquê está em `controle.run_diagnostico` |
| `ERRO` | job, no `except` | falha técnica (input incompleto, banco fora, parâmetro inválido, solver estourou). A mensagem fica em `run_status.erro` e o traceback no log do driver |

`FALHOU_QUALIDADE` ≠ `ERRO`. O primeiro é "o resultado não é confiável"; o segundo é "não deu
para chegar a um resultado". A ação do operador é diferente em cada caso — ver `03-producao.md`.

---

## 1.6 Os dois portões (não confundir)

| | Portão de **rodada** | Portão de **código** |
|---|---|---|
| Onde | `qualidade.checar()`, dentro do job | `pytest`, no CI antes do deploy |
| Pergunta | "este resultado é confiável?" | "esta mudança quebrou o comportamento?" |
| Quando | toda rodada, antes de publicar | todo merge |
| Se falhar | `FALHOU_QUALIDADE`, não publica | não faz deploy |

São coisas distintas e ambas obrigatórias. O portão de rodada tem 14 checagens críticas +
1 aviso (detalhadas em `05-testes-cobertura.md` §5).

---

## 1.7 Onde ficam os dados

| Schema | Quem escreve | Quem lê | Conteúdo |
|---|---|---|---|
| `input` | **front** (cadastro) | job | 16 tabelas de cadastro |
| `controle` | backend (`run_request`) e job (`run_status`, `run_diagnostico`) | backend | ciclo de vida da rodada |
| `public` | **job** | front/backend | 14 tabelas `otim_*` + 3 views |
| Blob (ADLS) | job (opcional) | auditoria | cópia integral em parquet, **incluindo o snapshot do cadastro** |

O snapshot do cadastro **não vai para o Postgres** — fica no blob. É a camada de reprodução:
permite refazer meses depois exatamente a mesma rodada.

---

## 1.8 Conceitos de domínio que o código assume

Sem estes, várias decisões do código parecem arbitrárias.

**Sub-bacia** — a unidade de decisão. Tem universo de ligações, ligações atuais, ticket
médio, vazão de contribuição e uma lista de obras possíveis.

**Cadeia até a ETE** — uma sub-bacia só **fatura** quando existe caminho completo de coleta
→ rede → tronco → EEE → linha de recalque → ETE. É por isso que uma obra pode ser
economicamente ótima e ainda assim ficar de fora: o elo que trava está em outro lugar.
`otim_obra.elo_que_trava` diz qual.

**Rateio por vazão** — uma obra de transporte serve várias sub-bacias. O CAPEX dela é
rateado entre elas na proporção da vazão. As frações de cada obra **somam exatamente 1** — o
portão de qualidade checa isso (desvio < 1e-6).

**CTS (Coletor de Tempo Seco)** — estrutura irmã da sub-bacia, pareada 1:1. Com
`USAR_CTS=True` ela vira um nó próprio com 4 obras (coletor, tronco, EEE, linha de recalque);
com `False`, a mesma demanda é vista de forma agregada. **A cobertura, a vazão e o universo
efetivo têm de ser idênticos nos dois modos** — só CAPEX e VPL mudam. Há 9 testes só sobre
essa invariância.

**ETE faseada** (`ETE_FASEADA`) — quando ligada, cada ETE vira K obras-módulo, priorizáveis
individualmente, e a capacidade cresce com o fluxo. Muda a **cardinalidade do problema**, não
é um ajuste fino. Default do motor: **desligada**.

**Régua de cobertura** (`input.cidade_operacional.unidade_cobertura`) — cada cidade mede
cobertura em `ligacoes`, `economias` **ou** `populacao`. Isso define a régua da meta e da
faixa de paridade daquela cidade. **A receita continua sempre em ligações**, em qualquer régua.

**Parcela industrial** — as colunas sem sufixo (`universo_ligacoes`, `receita_*`,
`vazao_contribuicao`) são o **TOTAL** = residencial + industrial. As colunas `*_industrial`
são a **parcela já contida nesse total**. Com `INCLUIR_INDUSTRIAL=True` usa-se o total como
está; com `False`, residencial = total − industrial. **Nunca somar.** Erro clássico: dupla
contagem. Está registrado como `COMMENT ON COLUMN` no DDL e tem 7 testes dedicados.

**Obra de terceiros** — CAPEX 0 com `tempo_execucao > 0`. A obra acontece e libera a cadeia,
mas não consome orçamento da Aegea. `otim_obra.responsavel` distingue.

**Paridade esgoto/água** — `tarifa_esgoto = ticket_medio(agua) × paridade`. A paridade vem de
uma tabela de faixas: vale a da **maior faixa cuja `cobertura_pct` ≤ cobertura realizada da
cidade no ano**. É endógena — depende do próprio plano.

**WACC** — por obra; quando vazio, herda o `wacc_medio` da unidade.
`otim_obra.wacc_origem` rotula de onde veio.

---

## 1.9 As quatro regras que não podem ser quebradas

1. **Motor puro** — nenhum I/O em `otimizador_capex_v62.py` / `otimizador_capex_cpsat63.py`.
2. **Suíte verde** — `pytest tests/` antes e depois de qualquer mudança. Não alterar a
   semântica dos testes nem os valores golden para "fazer passar".
3. **Caminho Excel preservado** — `ler_banco(path)` continua funcionando; é ele que garante
   que produção e desenvolvimento calculem igual.
4. **Credenciais só em Secret Scope** — nunca no código, nunca em widget, nunca em notebook.

---

## 1.10 Estado do pacote e pendências conhecidas

| Fase | Estado |
|---|---|
| 1 — Modelo de dados (`ddl_input.sql`) | pronto, com PKs/FKs/tipos revisados |
| 2 — Adaptador Postgres → Cenário | pronto; usa `.xlsx` temporário |
| 2b — `ler_banco` aceitando dict de DataFrames | **não feito** — proposital, ver abaixo |
| 3 — Portão de qualidade | pronto, 14 checagens críticas |
| 4 — Orquestração do job | pronto |
| 5 — Wheel + CI | **a fazer** |

**Por que a Fase 2b não foi feita:** o `.xlsx` temporário tem custo real (exige `openpyxl` no
cluster, escreve no disco local do driver, perde tipagem no caminho Postgres→pandas→Excel), mas
mexer nela significa mexer na assinatura de `ler_banco` — exatamente o ponto que a
retrocompatibilidade Excel e os testes protegem. Quando for feita, o caminho de menor risco é
aditivo: `ler_banco(fonte)` aceitando `dict` **ou** caminho, sem tocar no corpo da função.

**Dívidas menores registradas** (todas em `REVISAO_PRODUCAO.md`): `input` sem discriminador de
unidade — cada rodada lê o cadastro inteiro; DDL de resultado ainda aplicado como migration
manual; identificadores SQL por f-string em vez de `psycopg2.sql.Identifier`.

---

Próximo: **`02-integracao-backend.md`** (o que o backend escreve, dispara e lê).
