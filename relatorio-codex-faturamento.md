# Revisao adversarial: inicio do faturamento

Data da revisao: 2026-09-29.

## A causa

Confirmada.

No avaliador mensal do motor, `otimizador/dominio/otimizador_capex_v62.py`, o faturamento nao comeca em `chain_last + lag`. A linha efetiva e:

```python
inicio[o.id]=((chain_last[o.id]//12)+1)*12+o.lag
```

Isto ancora a cobranca em janeiro do ano seguinte ao mes em que a cadeia fica pronta, e so depois soma `lag`.

O proprio cabecalho do arquivo diz outra coisa: "Receita comeca em (ultima a ficar pronta + lag)". Portanto ha uma contradicao entre a regra documentada no topo e a regra executada.

Para `a1b94_1_1`, na rodada publicada `run_20260928_212514_faa350`:

- coleta `lig_a1b94_1_1`: pronta no mes 28, `2028-05`;
- `lag_meses`: 8;
- maior requisito pronto publicado para a cadeia: mes 28;
- ancora atual: `(28 // 12 + 1) * 12 = 36`, `2029-01`;
- inicio do faturamento atual: `36 + 8 = 44`, `2029-09`;
- regra direta `chain_last + lag`: `28 + 8 = 36`, `2029-01`.

A trava de capacidade da ETE participa da logica geral, mas nao deste caso. Para o sistema da sub-bacia (`Sistema 94 Serrana1`), o pacote `ete_a1e94#nova` esta pronto no mes 21 e libera 3 modulos, capacidade 291,99 L/s para vazao conectada de 237,33 L/s. Em `a1b94_1_1`, reconstituindo o `chain_last`, a capacidade nao empurra o marco: `chain_req = 28`, `chain_cap = 28`.

## O alcance

No banco publicado (`public.otim_obra` / `public.otim_subbacia`):

- rodadas com sub-bacias publicadas: 125;
- coletas faturando com `mes_inicio_faturamento`, `lag_meses` e `mes_pronta`: 15.230;
- casos em que `mes_inicio_faturamento - lag_meses` e multiplo de 12: 15.230;
- contraexemplos encontrados para essa assinatura: 0.

Isso confirma que a regra se repete nas rodadas publicadas: o faturamento sempre e armazenado como "janeiro de algum ano + lag".

O banco nao persiste `chain_last`, entao o atraso exato contra `chain_last + lag` nao e plenamente recuperavel para as 125 rodadas sem reexecutar o motor de cada uma. Usei `otim_dependencia` como aproximacao do maior requisito pronto: 15.167 casos ficam no intervalo esperado de 1 a 12 meses, media 6,81 meses e pior 12 meses; 63 casos em 32 rodadas ficaram fora do intervalo, o que indica que `otim_dependencia` e mapa de rateio/uso e nao uma serializacao fiel de `chain_last`.

No cenario real carregado do cadastro atual (`uA1`, mesma configuracao de `dev/rodar_simulacao_real.py`), reconstituindo `chain_last` dentro do motor:

- coletas faturando: 104;
- atraso medio exato da regra atual contra `chain_last + lag`: 8,19 meses;
- pior atraso: 12 meses;
- distribuicao: 1m=3, 2m=4, 3m=6, 4m=5, 5m=4, 6m=3, 7m=9, 8m=6, 9m=25, 10m=13, 11m=13, 12m=13;
- casos em que a trava de capacidade da ETE empurrou `chain_last`: 16;
- em `a1b94_1_1`: atraso exato 8 meses, sem empurrao da capacidade.

## Quem depende da regra atual

Se trocar a linha para `chain_last + lag`, estes pontos mudam de numero:

- `inicio_fat` retornado por `avaliar`, gravado em `otim_obra.mes_inicio_faturamento` e `otim_subbacia.mes_inicio_faturamento`.
- `_pv_receita(cen, o, inicio[o.id], ...)`: antecipa receita direta e indireta, muda VPL e ranking economico.
- `receita_ano`: muda o fluxo nominal por ano e todos os graficos/series que leem receita anual.
- `_fator_por_cobertura_realizada(cen, elig, inicio, anos)`: a cobertura que define a faixa de paridade passa a subir antes; isso tambem muda o `vp_efeito_base`.
- `opex_ini`: o OPEX caminha com a receita; antecipar faturamento antecipa OPEX das obras da cadeia e dos modulos de ETE que liberam capacidade.
- `vpl_por_subbacia`: CAPEX rateado nao muda pela data de faturamento, mas receita, efeito-base e OPEX rateado mudam.
- Persistencia/publicacao: `otimizador/infraestrutura/persistencia.py` apenas grava o resultado; as colunas publicadas mudam porque `inicio_fat` muda.
- `dashboard_otimizador_v2.economia_potencial`: ha uma segunda formula igual, `ini = ((pronto // 12) + 1) * 12 + col.lag`; se a regra mudar, este ponto precisa mudar junto ou a explicabilidade de potencial continuara usando a regra antiga.
- Metas/cobertura do objetivo: o bloco de cobertura anual usa `yop = chain_last // 12`, nao `inicio_fat`. Essa parte ja considera obra pronta, nao faturamento. Trocar `inicio_fat` nao muda diretamente esse `yop`, mas aumenta a tensao semantica: cobertura para meta continua no ano de conclusao da cadeia, enquanto receita/paridade passaria a seguir `chain_last + lag`.

O backend CP-SAT tambem usa a mesma ideia anualizada:

- `receita_pv(c,k) = M._pv_receita(cen,c,(k+1)*12+c.lag)`;
- `fatY(c,k) = ((k+1)*12+c.lag)//12`;
- OPEX de obras ligadas a coletas usa `((k+1)*12+cc.lag)//12`.

Ou seja: os dois modos concordam na regra "ano seguinte + lag", mas o CP-SAT trabalha em ano (`k`) e nao no mes exato do `chain_last`. Se a regra de negocio virar `chain_last + lag`, o CP-SAT nao pode ser corrigido so trocando `+1` por nada: ele precisara carregar o mes de conclusao, nao apenas o ano.

## O dinheiro

Medi no cenario `uA1`, configuracao de `dev/rodar_simulacao_real.py`, sem publicar rodada e sem alterar o pacote de trabalho. Usei uma copia temporaria do pacote apenas para trocar a linha de `inicio` para `chain_last + lag`; depois removi a copia.

Status dos dois solves: `OTIMO | obrig 7/7 | lexicografico: min metas_nao=0, 2a prior=cobertura`.

| Formula | VPL (R$ VP) | Receita total em VP (R$) | Receita nominal total (R$) | OPEX nominal total (R$) | Inicio `a1b94_1_1` |
|---|---:|---:|---:|---:|---:|
| atual: janeiro seguinte + lag | -26.634.406,34 | 234.703.974,45 | 523.322.020,98 | 170.895.334,66 | 44 |
| direta: `chain_last + lag` | -15.779.910,65 | 250.008.160,11 | 541.779.926,17 | 177.259.975,31 | 36 |
| diferenca | +10.854.495,69 | +15.304.185,65 | +18.457.905,19 | +6.364.640,66 | -8 meses |

Interpretacao: a troca antecipa receita e melhora o VPL, mas tambem antecipa OPEX; por isso o ganho de VPL e menor que o aumento de receita.

## A tela

O servidor (`app/infra/repositorios/nivel_detalhe.py`, `_fases`) devolve `mesesAteCobranca` como `lag_meses` puro e `dataInicioFaturamento` como a data calculada pelo motor. O front (`GraficoCronogramaObras.tsx`) exporta/mostra essas informacoes no cronograma/planilha de obras.

Com a regra atual, "conclusao", "ate a cobranca (meses)" e "inicio do faturamento" nao fecham a conta para o usuario. A tela deveria mostrar uma das duas coisas:

- se a regra atual for mantida: mostrar tambem a data de ancora, algo como "ancora da cobranca: janeiro do ano seguinte a cadeia pronta", e/ou "espera real ate cobranca";
- se a regra mudar para `chain_last + lag`: manter `lag_meses` como esta, porque ele passa a reconciliar com a data.

Do jeito atual, `lag_meses` e verdadeiro, mas incompleto: ele e so a segunda parcela da espera.

## Intencionalidade

Nao achei evidencia de decisao explicita para "janeiro do ano seguinte + lag".

Evidencias contra a intencionalidade:

- o comentario de topo do motor afirma `ultima pronta + lag`;
- o comentario inline da linha diz "JAN do ano seguinte", mas nao explica regra de negocio;
- `git log -G` mostra a formula ja presente na reorganizacao inicial preservada (`0cfd223`) e mantida por commits posteriores, sem mensagem de decisao;
- docs de resultado dizem apenas "quando a receita comeca";
- testes atuais nao prendem essa regra de faturamento; a suite passou sem exercitar esse contrato.

Suspeita: a regra parece resquicio de uma versao com receita agregada/anual, reforcada pelo CP-SAT que escolhe faturamento por ano (`k`) e pela agregacao/desconto anual (`Y = m // 12`). Nao encontrei prova documental suficiente para afirmar isso como fato.

## Verificacao

- `python -m pytest`: 178 passed, 13 skipped.
- Consulta ao banco: 125 rodadas; 15.230/15.230 casos com assinatura anual; 0 contraexemplos.
- Medicao financeira: carregamento read-only de `input.*`; nenhuma publicacao no banco; pacote temporario removido.
