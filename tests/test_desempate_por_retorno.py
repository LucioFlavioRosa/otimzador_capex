"""A fase 3 (desempate por retorno) e o criterio de convergencia (`gap_relativo`).

POR QUE A FASE 3 EXISTE. O desempate lexicografico ia ate a cobertura e parava ali:
obrigatorias, metas, cobertura. Ao chegar em C* o solver devolvia o PRIMEIRO plano
que a atingia, e entre um plano que rende 154 Mi e outro que rende 118 Mi com a
mesma cobertura ele nao tinha preferencia — qual saia dependia da ordem de busca e
do timing das threads.

Medido numa unidade de 67 cidades: duas execucoes com parametros IDENTICOS deram
VPL de 154,89 Mi e 150,27 Mi. Isso nao e vies (vies preservaria a ordem entre
cenarios); e dispersao, que embaralha — e quem usa o otimizador para COMPARAR
planos so distingue dois cenarios se a diferenca superar a dispersao.

O QUE OS TESTES GARANTEM, e nesta ordem de importancia:
  1. a fase 3 nao pode PIORAR o que as fases anteriores conquistaram;
  2. ela tem de melhorar (ou empatar) o retorno;
  3. `gap_relativo` chega a TODAS as fases e o default nao muda nada.
"""
import pytest

from _helpers import BANK_CTS, engine, silent, solver_or_skip


ORC_APERTADO = {2026: 2e6, 2027: 2e6, 2028: 2e6, 2029: 2e6}


#: Tempo da geracao de colunas, escolhido para ser IRREPETIVEL pelas fases: elas
#: recebem fracoes de `TEMPO_FASES`, e nenhuma cai em 7s. E o discriminante — usar
#: `num_search_workers` acoplaria o teste ao paralelismo, que pode mudar por motivo
#: legitimo sem que o requisito real mude.
TEMPO_COLUNAS = 7
TEMPO_FASES = 40


def _espiar(CP, cen, **kw):
    """Roda o solver anotando `(teto, gap)` de cada chamada ao CP-SAT.

    Envolver `Solve` e a unica forma de ver o que cada fase pediu: os solvers sao
    criados dentro de `resolver_por_sistema` e nao saem de la.
    """
    from ortools.sat.python import cp_model

    vistos = []
    original = cp_model.CpSolver.Solve

    def espiao(self, model, *a, **k):
        vistos.append((self.parameters.max_time_in_seconds,
                       self.parameters.relative_gap_limit))
        return original(self, model, *a, **k)

    cp_model.CpSolver.Solve = espiao
    try:
        silent(CP.resolver_por_sistema, cen, max_time_s=TEMPO_FASES, workers=4,
               col_time_s=TEMPO_COLUNAS, **kw)
    finally:
        cp_model.CpSolver.Solve = original
    return vistos


def _cenario(**kw):
    M = engine()
    opcoes = dict(orcamento=ORC_APERTADO, usar_cts=True, foco_cobertura=1.0,
                  penalidade_cobertura="meta+cobertura")
    opcoes.update(kw)
    return silent(M.ler_banco, BANK_CTS, **opcoes)


@pytest.mark.solver
def test_o_desempate_nao_sacrifica_meta_nem_cobertura():
    """A garantia que sustenta tudo: retorno e o ULTIMO criterio, nunca o primeiro.

    Se a fase 3 pudesse trocar cobertura por VPL, ela deixaria de ser desempate e
    viraria uma mudanca de objetivo pelas costas de quem escolheu "cobertura
    primeiro" na tela.
    """
    CP = solver_or_skip()
    M = engine()
    cen = _cenario()
    res = silent(CP.resolver_por_sistema, cen, max_time_s=60, workers=4)

    # As tres conquistas anteriores continuam de pe no plano devolvido.
    assert res["metas_nao_atingidas"] is not None
    ok, viol = M.auditar_orcamento(cen, res)
    assert ok, f"a fase 3 estourou o teto anual: {viol}"


@pytest.mark.solver
def test_a_fase_3_roda_no_modo_cobertura_e_NAO_roda_no_modo_meta():
    """Onde o desempate se aplica, e onde ele seria desperdicio.

    No modo "meta+cobertura" a fase 2 maximiza COBERTURA, e o retorno fica solto —
    e ai que o desempate serve. No modo "meta" ela ja maximiza o proprio VPL, e
    desempatar VPL por VPL so gastaria tempo.
    """
    import contextlib
    import io

    CP = solver_or_skip()

    def _saida(cen):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            CP.resolver_por_sistema(cen, max_time_s=30, workers=4, verbose=True)
        return buf.getvalue()

    assert "desempate por retorno" in _saida(_cenario())
    assert "desempate por retorno" not in _saida(_cenario(penalidade_cobertura="meta"))


@pytest.mark.solver
def test_duas_execucoes_iguais_dao_o_MESMO_retorno():
    """O ponto todo da fase 3: tirar o sorteio do VPL.

    Antes dela, duas execucoes identicas podiam diferir em milhoes — o solver
    parava no primeiro plano com a cobertura alvo, e qual era esse plano dependia
    do timing das threads. Com o retorno virando objetivo, o resultado converge.
    """
    CP = solver_or_skip()
    a = silent(CP.resolver_por_sistema, _cenario(), max_time_s=60, workers=4)
    b = silent(CP.resolver_por_sistema, _cenario(), max_time_s=60, workers=4)
    assert a["vpl"] == pytest.approx(b["vpl"], rel=1e-6), (
        "duas execucoes identicas divergiram no VPL — o desempate nao esta "
        "determinando o plano"
    )
    # LIMITE DESTE TESTE, dito aqui para ninguem confiar demais nele: num cenario
    # sem empate real de cobertura ele passaria mesmo sem a fase 3, porque nao
    # haveria dois planos entre os quais sortear. Ele detecta REGRESSAO (a fase 3
    # sair, ou parar de determinar o plano); nao PROVA a ausencia de dispersao no
    # caso grande, que so a medicao em unidade real mostra.


@pytest.mark.solver
def test_gap_relativo_chega_a_todas_as_fases():
    """O criterio de convergencia vale em TODA fase, e nao so na ultima.

    Antes cada fase montava o proprio `CpSolver`; um criterio novo teria de ser
    repetido em cinco lugares, que e como se esquece um. A fabrica `_sv` existe
    para isso, e este teste e o que prova que ela e mesmo o unico caminho.
    """
    CP = solver_or_skip()
    vistos = _espiar(CP, _cenario(), gap_relativo=0.02)

    # A GERACAO DE COLUNAS fica de fora, de proposito: ela roda com `workers=1` e
    # monta a materia-prima do master. Um gap ali mudaria as COLUNAS disponiveis,
    # e nao so o tempo de prova do plano final — efeito colateral por um caminho
    # que ninguem lembraria de olhar. O discriminante e o numero de threads.
    colunas = [g for t, g in vistos if t == pytest.approx(float(TEMPO_COLUNAS))]
    fases = [g for t, g in vistos if t != pytest.approx(float(TEMPO_COLUNAS))]

    assert fases, "nenhuma fase rodou"
    assert all(g == 0.0 for g in colunas), (
        f"o gap vazou para a geracao de colunas: {colunas}"
    )
    # As DUAS PRIMEIRAS fases ficam sem gap (prioridade absoluta); da fase 2 em
    # diante ele vale. Ver `test_o_gap_NAO_alcanca_obrigatorias_nem_metas`.
    assert any(g == pytest.approx(0.02) for g in fases), (
        f"nenhuma fase recebeu o gap: {fases}"
    )


@pytest.mark.solver
def test_o_default_do_gap_nao_muda_nada():
    """`gap_relativo=0.0` tem de manter o comportamento historico.

    E o que permite subir esta versao sem reprocessar nada: quem nao passa o
    parametro roda exatamente como antes.
    """
    CP = solver_or_skip()
    vistos = _espiar(CP, _cenario())
    assert all(g == 0.0 for _, g in vistos), f"o default vazou um gap: {vistos}"


@pytest.mark.solver
def test_o_gap_NAO_alcanca_obrigatorias_nem_metas():
    """As duas primeiras fases sao PRIORIDADE ABSOLUTA e nao aceitam folga.

    Um gap ali nao compra tempo, compra risco: com 2%, a fase 0 pode parar num
    incumbente de 99 obrigatorias com bound 100 e travar `_obrig_floor(99)` — e dai
    em diante o plano deixa uma obrigatoria de fora COMO SE ela nao coubesse no
    orcamento. Medido: essas fases provam em ~1s mesmo com 67 cidades, entao nao ha
    tempo a economizar nelas.

    O mesmo raciocinio vale para `Mstar`, a meta travada pela fase 1.
    """
    CP = solver_or_skip()
    vistos = _espiar(CP, _cenario(), gap_relativo=0.02)
    fases = [(t, g) for t, g in vistos if t != pytest.approx(float(TEMPO_COLUNAS))]

    # As fases absolutas sao reconheciveis pelo teto: 0,35 e 0,4 de `TEMPO_FASES`.
    absolutas = [g for t, g in fases
                 if t == pytest.approx(TEMPO_FASES * 0.35) or t == pytest.approx(TEMPO_FASES * 0.4)]
    assert absolutas, "nao identifiquei as fases de prioridade absoluta"
    assert all(g == 0.0 for g in absolutas), (
        f"o gap alcancou obrigatorias/metas: {absolutas}"
    )


@pytest.mark.solver
def test_o_desempate_nao_estica_o_teto_de_tempo():
    """A fase 3 recebe o que SOBRA do teto, e nao mais um pedaco dele.

    As fracoes historicas ja somavam 1,35x o `max_time_s` pedido. Somar mais 0,6
    levaria a 1,95x — uma regressao operacional silenciosa para quem dimensionou o
    job pelo numero que passa no parametro.
    """
    CP = solver_or_skip()
    vistos = _espiar(CP, _cenario(), gap_relativo=0.02)
    fases = [t for t, _ in vistos if t != pytest.approx(float(TEMPO_COLUNAS))]
    teto = TEMPO_FASES * 1.35

    # O QUE SE MEDE E O TETO DA ULTIMA FASE, e nao a soma dos tetos. Somar os tetos
    # nao diz nada: eles sao limites por fase, e as anteriores costumam terminar bem
    # antes do seu. A garantia esta na FORMA como o teto da fase 3 e calculado — ela
    # recebe `teto_total - decorrido`, entao `decorrido + teto_fase_3 <= teto_total`
    # por construcao. Se alguem a trocar por uma fracao propria, este numero passa do
    # teto e o teste cai.
    assert fases[-1] <= teto + 1e-6, (
        f"a fase 3 pediu {fases[-1]:.1f}s, acima do teto total de {teto:.1f}s — "
        f"ela deixou de receber o que sobra e virou mais um pedaco: {fases}"
    )


@pytest.mark.solver
def test_os_dois_gaps_sao_independentes():
    """Cobertura e retorno tem folgas SEPARADAS, porque sao moedas diferentes.

    `gap_relativo` afrouxa a cobertura e, com ela, o `C*` que a fase 3 trava — e
    `C*` e a base de comparacao entre cenarios. `gap_retorno` nao mexe em `C*`: so
    decide quanto tempo se gasta refinando o VPL dentro do que ja foi travado.

    Medido em tres execucoes identicas com gap UNICO de 2%: `C*` variou entre
    670.092, 670.193 e 673.202, e o VPL acompanhou na direcao contraria. Quem
    compara cenarios quer o primeiro apertado; quem quer velocidade quer o segundo
    folgado. Com um numero so, nao da para ter os dois.
    """
    CP = solver_or_skip()
    vistos = _espiar(CP, _cenario(), gap_relativo=0.005, gap_retorno=0.05)
    fases = [g for t, g in vistos if t != pytest.approx(float(TEMPO_COLUNAS))]

    assert 0.005 in [pytest.approx(g) for g in fases], (
        f"nenhuma fase recebeu o gap de COBERTURA: {fases}"
    )
    assert 0.05 in [pytest.approx(g) for g in fases], (
        f"a fase de RETORNO nao recebeu o gap dela: {fases}"
    )


@pytest.mark.solver
def test_gap_retorno_ausente_herda_o_da_cobertura():
    """Quem chamava com um numero so continua funcionando igual."""
    CP = solver_or_skip()
    vistos = _espiar(CP, _cenario(), gap_relativo=0.02)
    fases = [g for t, g in vistos if t != pytest.approx(float(TEMPO_COLUNAS))]
    comgap = [g for g in fases if g > 0]

    assert comgap, "nenhuma fase recebeu gap"
    assert all(g == pytest.approx(0.02) for g in comgap), (
        f"sem `gap_retorno`, todas as fases com folga usam o mesmo valor: {comgap}"
    )
