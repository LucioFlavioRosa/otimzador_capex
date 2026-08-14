# Testes do otimizador de CAPEX

Malha de regressão para garantir que o otimizador continua fazendo o que deve — e que mudanças
futuras não quebrem o comportamento atual.

## Como rodar

Da **raiz do projeto** (onde estão o `main.py`, o `pytest.ini` e o pacote `otimizador/`):

```bash
pip install -r requirements-prod.txt     # pytest + ortools + pandas + matplotlib + openpyxl
pytest                                    # 127 testes: 114 passed, 13 skipped
pytest -m "not solver"                    # só os que não precisam de OR-Tools
pytest -m solver                          # só os do solver
pytest -m "not slow"                      # pula a separabilidade (mais lento)
```

Este README cobre os testes do **motor**. A camada de produção (`test_producao.py`,
`test_publicacao_postgres.py`) está documentada em [`../docs/05-testes-cobertura.md`](../docs/05-testes-cobertura.md),
e o passo a passo de execução — inclusive os testes que precisam de Postgres — em
[`../docs/04-testes-executar.md`](../docs/04-testes-executar.md).

Os 13 skips com tudo instalado são os 12 de `test_publicacao_postgres.py`, que exigem
`OTIMIZADOR_PG_TESTE`, e 1 de `test_nucleo.py`, que importa a suíte legada. Sem OR-Tools os skips
sobem para 36; sem matplotlib, para 23 — dependência opcional ausente **sobe skip, nunca falha**. No Colab, rode `!pip install ortools pytest` e depois `!pytest` na pasta.

## O que está coberto

**`test_cts.py` — CTS ligada × desligada, dois cenários (13 testes)**
- Para a sub-bacia, a **única** diferença é qual coluna é lida: com o coletor, as exclusivas;
  sem ele, as oito `*_com_cts` (exclusivo + área sobreposta). Nada é somado da linha da CTS.
- Os dois cenários **não** têm a mesma demanda: sem o coletor, a área que só ele alcançava não é
  atendida por ninguém. Vazão, receita e população são dado da sub-bacia e não são herdadas.
- Base sem as colunas `*_com_cts`: usa o universo exclusivo e o motor emite `[ALERTA]`.
- O que sempre difere: o modo ligado tem 4 obras a mais por CTS e CAPEX maior (exatamente o CAPEX
  das obras da CTS). O VPL depende de qual ticket é maior — o da sub-bacia que absorve ou o do
  coletor —, e o teste não fixa sentido.
- Estrutura: CTS viram nós com `is_cts`; cada uma tem os 4 componentes certos (Coletor de tempo
  seco + Tronco + EEE + Linha de recalque), sendo o Coletor a âncora de coleta.
- Retrocompatibilidade: banco sem CTS dá resultado idêntico com `usar_cts` True ou False.

**`test_nucleo.py` — regras do motor**
- Perfil de OPEX côncavo: começa no piso (50%), sobe desacelerando e atinge o máximo na maturação.
- CAPEX = quantidade × preço unitário.
- WACC: todo elemento Aegea tem WACC e origem rotulada; WACC vazio consome o `wacc_medio` da unidade.
- Janela de CAPEX: `anos_extra_conclusao` configura a cauda; carry-forward da janela disponível.
- Leitura estrita: nome de coluna antigo **não** é aceito como fallback.
- (solver) Respeita o teto anual; com orçamento folgado reproduz o build-all; separabilidade por
  cidade fecha em ~zero.

**`test_classe.py` — recorte da meta (`cobertura_so_residencial`, 10 testes)**
- O recorte acaba na **cobertura**: com ele ligado, universo e base atendida saem das colunas
  `*_residencial`, medidas na base comercial.
- **CAPEX, vazão, receita e VPL não mudam** — a mesma carteira de obras rende o mesmo. É a
  invariante central deste arquivo.
- A obra carrega duas quantidades: `lig` (habilitadas, alimenta a receita) e `lig_cob` (contam
  para a meta). Sem recorte as duas são iguais.
- Cobertura por **ligações** e por **economias** encolhe nas duas pontas — universo e base. Por
  **população** fica intacta: indústria não mora, então o universo de população já é residencial.
- Banco sem as colunas `*_residencial` → os dois modos são idênticos, e o motor avisa em voz alta.

**`test_derivadas.py` — colunas calculadas pela engine**
- `ligacoes_novas_obras = universo − atuais`; a engine **ignora** o valor do banco e usa o derivado.

**`test_regressao_golden.py` — números congelados**
- Trava VPL, CAPEX, cobertura, universo, vazão e nº de obras do **build-all** (determinístico,
  independe do orçamento), ligado e desligado. Qualquer mudança que altere o resultado é sinalizada.
- Para o **solver** não travamos um número fixo (variaria entre versões de OR-Tools): checamos o
  invariante de otimalidade — o VPL do solver fica **≥ build-all** (nunca pior; o build-all é o
  piso "constrói tudo") e cumpre as metas. Isso vale porque o solver larga obra que destrói valor.
- Para atualizar o golden de propósito: `python tests/atualiza_golden.py` e cole o bloco (revisando).

## Bases de teste (fixtures)

A suíte é **autossuficiente**: lê só de `tests/fixtures/`, sem depender de subir os bancos grandes.

- `banco_teste_CTS_poc_v2.xlsx` — banco pequeno **com CTS** (2 coletores). Alimenta os testes de
  CTS e o golden.
- `banco_fixture_testes.xlsx` — banco pequeno **sem CTS** e com **mix de WACC** (≈60% vazios que
  herdam o `wacc_medio` + ≈40% próprios). Alimenta a retrocompatibilidade e a regra de WACC médio.
- `banco_fixture_classe.xlsx` — **colunas residenciais** menores que o total em b1/b3, cidade c1
  medindo em **economias** e c2 em **população**. Alimenta o recorte da meta e a cobertura por
  unidade.

Assim os testes rodam em qualquer sessão (Colab, local, CI) sem precisar do banco regional.

## Marcadores

- `solver` — precisa de OR-Tools (pula se ausente).
- `slow` — teste mais lento (decomposição por cidade).
