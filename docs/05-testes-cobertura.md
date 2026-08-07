# 5. O que os testes cobrem

Público: quem vai mudar o código e precisa saber **o que já está protegido** e **o que não
está**. Também serve como especificação executável: cada teste é uma regra de negócio escrita.

Panorama: **107 testes em 7 arquivos**, mais o **portão de qualidade por rodada** (14 checagens
críticas), que é um mecanismo diferente e complementar — ver §5.8.

---

## 5.1 `test_nucleo.py` — regras do motor (9 testes)

O que quebraria em silêncio se ninguém travasse:

- **Perfil de OPEX côncavo** — começa no piso, sobe desacelerando, atinge o máximo na maturação
  e **nunca decresce**. Também: maturação ≤ 1 mês ⇒ sempre no máximo.
- **CAPEX = quantidade × preço unitário.** A conta que o cadastro assume.
- **WACC** — todo elemento Aegea tem WACC **e origem rotulada**; WACC vazio herda o
  `wacc_medio` da unidade. Sem isso, uma obra sem WACC seria descontada a zero.
- **Janela de conclusão** — `ANOS_EXTRA_CONCLUSAO` configura a cauda para terminar o que
  começou dentro da janela de CAPEX.
- **Leitura estrita de nomes de coluna** — um nome antigo **não** é aceito como fallback. É o
  teste que garante que renomear coluna no cadastro falha alto em vez de produzir dado vazio.
- *(solver)* **Respeita o teto anual.**
- *(solver)* **Nunca pior que o build-all** com orçamento folgado, e cumpre as metas
  (déficit ≈ 0).
- *(solver, slow)* **Separabilidade por cidade é exata** — a decomposição fecha em ~zero. Este
  é o teste que **pula** por falta da suíte legada.

## 5.2 `test_cts.py` — CTS ligado × desligado (9 testes)

O CTS pode ser visto como nó próprio (`USAR_CTS=True`) ou agregado (`False`). É a **mesma
demanda física em duas representações**, e o conjunto de testes fixa o que tem de bater e o que
tem de diferir:

| Tem de ser **idêntico** | Tem de **diferir** |
|---|---|
| cobertura | 4 obras a mais por CTS no modo ligado |
| vazão | CAPEX maior — **exatamente** o CAPEX das obras da CTS |
| universo efetivo | VPL menor no modo ligado |

Mais: cada CTS tem os 4 componentes certos (coletor de tempo seco + tronco + EEE + linha de
recalque), sendo o coletor a âncora de coleta; e **retrocompatibilidade** — banco sem CTS dá
resultado idêntico com a flag em `True` ou `False`.

Se você mexer em rateio, topologia ou cobertura, é aqui que costuma quebrar primeiro.

## 5.3 `test_classe.py` — residencial × industrial (7 testes)

A regra de leitura mais fácil de errar do sistema (dupla contagem). Travado:

- **Banco sem colunas `*_industrial` → os dois modos são idênticos** (retrocompatível).
- **Só residencial: CAPEX igual**, receita e vazão caem — e a queda de vazão é **exatamente** a
  parcela industrial. Não é aproximação.
- **Cobertura por ligações cai; por economias cai; por população fica intacta** (indústria ≈ 0
  habitantes). A parcela industrial de economias é estimada pela proporção das ligações
  industriais.

Ou seja: os três valores possíveis de `unidade_cobertura` têm comportamento fixado.

## 5.4 `test_derivadas.py` — colunas calculadas (2 testes)

`ligacoes_novas_obras = universo − atuais`, e **o valor gravado no banco é ignorado**. Existem
dois testes porque a segunda metade é contraintuitiva: alguém "corrigir" o número no cadastro
não muda nada, e é melhor que isso esteja escrito.

## 5.5 `test_regressao_golden.py` — números congelados (4 testes)

Trava VPL, CAPEX, cobertura, universo, vazão e nº de obras do **build-all** — determinístico e
independente do orçamento — nos modos ligado e desligado. Qualquer mudança que altere o
resultado numérico é sinalizada.

Para o **solver** não há número fixo (variaria entre versões do OR-Tools): checa-se o
invariante de otimalidade `VPL(solver) ≥ VPL(build-all)`.

Este é o teste que decide se uma refatoração foi neutra. Atualizar o golden é uma decisão
consciente — ver `04-testes-executar.md` §4.6.

## 5.6 `test_producao.py` — a camada de produção (64 testes)

Nenhum precisa de banco. Cobrem os bugs mais caros encontrados na revisão do pacote.

### Tradução de `run_request.params` → kwargs (14 testes)

- **Todo kwarg do `MAPA_PARAMS` existe na assinatura de `ler_banco`** — comparação via
  `inspect.signature`. Se alguém renomear um parâmetro do motor, quebra aqui e não em produção
  ignorando o parâmetro em silêncio.
- **Chave ausente não vira default do job.** O bug original: o job usava `ete_faseada=True`
  (motor: `False`) e `foco_cobertura=1.0` (motor: `None`). O segundo satura o peso de
  cobertura — uma `run_request` sem essa chave rodava **"só cobertura" em vez de "só VPL"**, e
  o resultado saía diferente do notebook com o mesmo cadastro.
- **Chave desconhecida é erro** — um `orcamento` minúsculo passava batido e a rodada saía sem
  teto.
- Chaves do job (`USUARIO`, `MAX_TIME_S`, `WORKERS`) não viram kwarg do motor.
- Todas as chaves conhecidas são traduzidas.
- **`ORCAMENTO` vindo do JSONB** — a chave `"2026"` (string, como o Postgres devolve) vira
  `int`, que é o único formato que o motor reconhece como **cronograma anual**; orçamento por
  unidade **não** é convertido, e os demais formatos passam intactos.
- **Teto anual é exigido** — teto infinito é recusado com o nome da regional na mensagem. Sem
  isso, a rodada saía sem restrição anual e o CP-SAT estourava com `OverflowError` opaco.

### Portão de qualidade (13 testes)

- **Aprova uma rodada sadia.**
- **Aceita os status que o CP-SAT realmente devolve** — `OTIMO`, `OTIMO | OBRIG 3/3`,
  `VIAVEL(limite de tempo)`, `VIAVEL(...) | so cobertura`. A checagem original comparava com
  `("OPTIMAL","FEASIBLE")`, que **nunca** é verdade: o portão reprovava 100% das rodadas boas e
  nada jamais seria publicado.
- **Reprova `SEM SOLUCAO`.**
- **Reprova tabela obrigatória vazia**, `run_id` divergente entre tabelas, duplicata de PK e
  CAPEX sem teto (`teto_capex = INF`).

### `rodar()` fim a fim, com o Postgres dublado (7 testes)

Nenhum teste chamava a orquestração. Foi assim que passaram despercebidos um `import os`
ausente — `NameError` só em execução, que `py_compile` não pega — e um `arquivo_fonte=`
esquecido na materialização. Agora `rodar()` é exercitado com `publicacao` e
`carregar_postgres` substituídos por dublês: ordem dos passos, o que é passado a quem,
presença das `snapshot__*` no que vai ser publicado, `criar_schema=False`, **nome único** do
snapshot temporário (dois jobs no mesmo driver não se atropelam), a remoção desse arquivo no
fim, e os dois ramos de saída — `FALHOU_QUALIDADE` **não publica**, e erro técnico marca
`ERRO` e **re-levanta** a exceção.

### Materialização e notificação (6 testes)

- **Propagação do `run_id`** — `materializar(..., run_id=X)` marca **todas** as tabelas com
  `X`. Sem isso, cada rodada gerava um id novo: `controle.*` e `public.otim_*` deixavam de
  casar e cada retry publicava de novo em vez de substituir.
- **Snapshot do cadastro** — a materialização gera as `snapshot__*` a partir do arquivo fonte.
- **`blob_uri` aponta para um caminho que existe** — o ponteiro da auditoria. Até 2026-08-04
  gravava `<destino>/run_id=<rid>`, que a gravação nunca cria: `salvar` particiona por `run_id`
  **dentro** de cada tabela (`<destino>/<tabela>/run_id=<rid>/`). Não havia perda de dado, mas
  quem seguisse `otim_meta.blob_uri` para achar o snapshot congelado não achava nada.
- **Tabela obrigatória vazia é erro; tabela tolerada vazia só avisa.**
- **Falha ao notificar não derruba a publicação** — a notificação é pós-commit; o dado já está
  gravado quando ela roda.

### Idempotência da cópia em blob (24 testes)

O `DELETE ... WHERE run_id` tornava a rodada idempotente no Postgres, e a documentação
generalizava isso para "tudo é idempotente". O blob não era: a gravação via Spark era
`mode("append")` particionada por `run_id`, ou seja, o retry acrescentava arquivos **dentro**
da partição em vez de trocá-la. Como o blob é escrito **antes** da transação do Postgres,
bastava a rodada falhar depois dele para o parquet ficar em dobro — e quem seguisse o
`blob_uri` meses depois encontraria o dobro das linhas. Corrigido em 2026-08-06.

- **Regravar a mesma rodada substitui a partição** — 3 linhas gravadas duas vezes continuam 3.
- **Não sobra formato antigo na partição** — `carregar()` lê **tudo** que estiver na pasta,
  então um `dados.csv` deixado pelo fallback conviveria com o `dados.parquet` novo.
- **Outras rodadas ficam intactas** — é a diferença entre idempotente e destrutivo.
- **A partição sai do dado, não do `run_meta`** — sem `run_meta` no conjunto, a versão anterior
  inventava um id (`novo_run_id()`) e gravava numa pasta que não correspondia ao dado.
- **Ramo Spark (o de produção): apaga a partição, depois acrescenta** — e `append`, não
  `overwrite`, porque overwrite sem partição dinâmica levaria a pasta inteira junto.
- **Sem `particionar_por_run` não apaga nada** — guarda contra o defeito inverso.
- **Delta substitui pelo log (`replaceWhere`), nunca apagando pasta** — apagar `run_id=<rid>/`
  num Delta corromperia o log; e num Delta que ainda não existe a primeira gravação é `append`,
  porque não há o que substituir.
- **`salvar_delta` substitui a rodada por padrão** — o default era `modo="append"`, o mesmo
  defeito na API que a documentação recomenda para o Databricks. `modo` explícito ainda dá
  append cru a quem pedir.

E o que a revisão do próprio conserto encontrou — a substituição usa o `run_id` como
**caminho** e como **literal SQL**, e ele vem do backend numa coluna `text` sem gramática:

- **`run_id` hostil é recusado** — `r1' OR run_id <> 'r1` fecharia o literal do
  `replaceWhere` e casaria com **todas** as rodadas: o `overwrite` levaria a tabela Delta
  inteira, o que seria pior que o bug original, que só duplicava. `..`/`/` desviariam o
  diretório apagado, e espaço/`=` fariam a pasta real ter outro nome (o Spark escapa valor
  de partição ao gravar) — com a substituição virando no-op e a duplicação voltando calada.
- **`run_id` normal continua passando**, e **`novo_run_id()` passa na própria guarda** —
  trava a coerência entre gerador e validador.
- **Dois `run_id` no mesmo conjunto é erro** — "substituir a rodada" não tem significado:
  apagaria duas partições numa chamada, ou poria as linhas de uma dentro da pasta da outra.
  O portão já barra isso antes de publicar, mas `salvar` é API pública.
- **Coluna `run_id` toda nula é erro** — antes caía num fallback que virava `run_id = ''`,
  append disfarçado de substituição.
- **Falha ao consultar o catálogo propaga** — engolir a exceção e responder "não existe"
  escolhia o `append`, ou seja, a duplicação de volta em silêncio justo quando o metastore
  está com problema. Só a ausência da API (Spark antigo) ainda vira "não existe".

## 5.7 `test_publicacao_postgres.py` — Postgres de verdade (12 testes)

Pulam sem `OTIMIZADOR_PG_TESTE`. **Ainda não foram executados** — ver
`04-testes-executar.md` §4.4.

### Idempotência

- **Republicar o mesmo `run_id` não duplica** — `otim_meta` continua com 1 linha e a contagem
  de cada tabela de detalhe não muda.
- **Republicar com menos obras apaga as antigas** — é o teste do `ON DELETE CASCADE`. Se o
  cascade não existir no banco (DDL escrito à mão divergindo do gerado), falha aqui.
- **Outra rodada não é afetada** — o `DELETE` é por `run_id`.

### Transação

- **Falha no meio não grava nada** — injeta uma coluna inexistente numa tabela publicada
  *depois* de `run_meta` e `run_obra`; o que já entrou tem de sumir no rollback.
- **Publicação e `SUCESSO` entram no mesmo commit** — abre a conexão, publica, marca, e levanta
  uma exceção **depois** dos dois: ambos voltam juntos. É a propriedade em que o job se apoia
  para o estado observável nunca mentir.
- **A conexão do chamador não é fechada** — o bug que impedia compor as duas escritas.
- **`publicar(status_controle=...)` commita e desfaz os dois juntos** — é o caminho que o
  job usa de fato, com blob e notificação na ordem certa.

### Controle

- **Upsert de status** não vira insert duplicado.
- **Status fora do domínio é rejeitado** pelo `CHECK` do DDL (ex.: `CONCLUIDO`, que é o
  vocabulário de `otim_meta`, não o de `run_status`).
- **Diagnóstico idempotente** — reprocessar não acumula relatórios.
- **Diagnóstico de outra rodada sobrevive.**

---

## 5.8 O outro portão: `qualidade.checar()`

Não é pytest — roda **em toda rodada**, depois de otimizar e antes de publicar. Se qualquer
checagem **crítica** falhar, o job marca `FALHOU_QUALIDADE`, grava o relatório em
`controle.run_diagnostico` e **não publica**. São **14 críticas + 1 aviso**.

> Até a revisão de 2026-07-27, duas destas checagens (déficit de meta e cobertura
> não-negativa) **nunca executavam**: procuravam as colunas `deficit` e `cobertura`, mas
> `persistencia` grava `deficit_ligacoes` e `cobertura_pct`. Como toda checagem é
> condicional à existência da coluna, elas silenciavam em vez de falhar — o relatório vinha
> com 12 linhas e ninguém notava as duas faltando. `test_portao_roda_14_checagens_criticas`
> agora trava esse número.

| # | Checagem | Nível | O que pega |
|---|---|---|---|
| 0 | Tabelas obrigatórias presentes e não vazias | crítico | materialização degradada. Sem ela, as outras checagens (todas condicionais) simplesmente não rodam e o portão diria "OK" com 2 checagens |
| 0b | `run_id` único em todas as tabelas | crítico | violação de FK que viraria erro opaco dentro da transação |
| 0c | Sem duplicatas nas PKs | crítico | erro de constraint no meio do `INSERT`, com mensagem que não diz de onde veio |
| 1 | Status do solver ∈ {OTIMO, VIAVEL} | crítico | solver não achou solução viável |
| 2 | VPL: soma por sub-bacia = VPL do plano | crítico | decomposição inconsistente |
| 3 | CAPEX: `run_ano` = `run_meta` | crítico | reconciliação |
| 4 | CAPEX: `run_mes` = `run_ano` | crítico | curva mensal inconsistente |
| 5 | CAPEX: `run_cidade_ano` = `run_ano` | crítico | consolidação por cidade inconsistente |
| 6 | Frações de rateio somam 1 por obra | crítico | rateio por vazão quebrado (desvio < 1e-6) |
| 7 | Teto anual respeitado | crítico | plano estourou o orçamento |
| 7b | Teto definido em todos os anos | crítico | rodada sem orçamento — a checagem 7 passaria trivialmente contra `INF` |
| 8 | Colunas-chave sem NaN | crítico | dado faltando em `run_obra`/`run_subbacia`/`run_ano` |
| 9 | Déficit de meta não-negativo | crítico | conta de meta invertida |
| 10 | Cobertura não-negativa | crítico | idem |
| 11 | Plano não-vazio | **aviso** | zero obras construídas — hoje **não** barra a publicação |

Tolerância: **R$ 0,01** (absoluta). Adequada na escala atual — o erro de ponto flutuante em
somas na casa de R$ 10⁹ fica em ~10⁻⁴. Se o horizonte crescer uma ordem de grandeza, vale
trocar por `max(0.01, 1e-9 × |referência|)`.

**Decisão pendente de negócio:** "Plano não-vazio" é aviso. Uma rodada que não constrói nada é
resultado legítimo (orçamento zero, tudo destrói valor) ou é falha? Se for falha, mude o nível
para `critico` em `qualidade.py`.

---

## 5.9 O que **não** está coberto

Honestidade sobre as lacunas, para ninguém confiar demais na suíte verde:

| Lacuna | Risco | Mitigação hoje |
|---|---|---|
| `otimizador/infraestrutura/carregar_postgres.py` sem teste de integração | o adaptador de leitura nunca leu de um Postgres real | `_roundtrip_xlsx` prova que a materialização em xlsx não altera o Cenário; a leitura em si é `SELECT *` |
| `job_databricks.rodar()` contra um banco real | a orquestração é exercitada com o Postgres **dublado** (§5.6); nunca rodou contra um banco de verdade em teste automatizado | o `main.py smoke` faz esse caminho, mas é manual — não é gate |
| `otimizador/apresentacao/leitor_v2.py` | contrato de leitura sem teste automatizado | é o módulo que prova o contrato — merece teste |
| `otimizador/apresentacao/dashboard_otimizador_v2.py` | explicabilidade sem teste | usado só pela materialização |
| Service Bus / webhook | notificação sem teste | reexecutável por natureza — reenviar não altera dado |
| DDL de `input` | PKs e FKs novas nunca aplicadas a dados reais | a migration tem consultas de diagnóstico |
| Concorrência | dois jobs no mesmo `run_id` em paralelo | não testado; **evitar** (ver `03-producao.md` §3.8) |

**Onde investir primeiro**, em ordem de retorno:

1. **Executar** os 12 testes de `test_publicacao_postgres.py` (já escritos) e corrigir o que
   aparecer.
2. Teste fim-a-fim de `rodar()` contra um Postgres com o cadastro de fixture carregado — pega a
   orquestração inteira, inclusive `carregar_postgres`.
3. Teste de `leitor_v2.py` sobre um `tabs` materializado: se as telas se reconstroem, o
   contrato com o backend está completo.

---

## 5.10 Resumo para quem vai mexer

| Se você mexer em… | Rode antes de tudo |
|---|---|
| motor (`otimizador/dominio/otimizador_capex_v62.py`) | `pytest tests/` inteiro — em especial o golden |
| solver (`otimizador/dominio/otimizador_capex_cpsat63.py`) | `pytest -m solver tests/` |
| rateio / topologia / cobertura | `test_cts.py` + `test_classe.py` |
| leitura do cadastro / nomes de coluna | `test_derivadas.py` + `test_nucleo.py` |
| contrato de parâmetros do job (`otimizador/aplicacao/job_databricks.py`) | `test_producao.py` |
| `otimizador/infraestrutura/publicacao.py` / DDL | `test_publicacao_postgres.py` **com** Postgres |
| `otimizador/dominio/qualidade.py` | `test_producao.py` (os 13 testes de portão) |
