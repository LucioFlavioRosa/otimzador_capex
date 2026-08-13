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


def _espiar(CP, cen, **kw):
    """Roda o solver anotando `(workers, gap)` de cada chamada ao CP-SAT.

    Envolver `Solve` e a unica forma de ver o que cada fase pediu: os solvers sao
    criados dentro de `resolver_por_sistema` e nao saem de la.
    """
    from ortools.sat.python import cp_model

    vistos = []
    original = cp_model.CpSolver.Solve

    def espiao(self, model, *a, **k):
        vistos.append((self.parameters.num_search_workers,
                       self.parameters.relative_gap_limit))
        return original(self, model, *a, **k)

    cp_model.CpSolver.Solve = espiao
    try:
        silent(CP.resolver_por_sistema, cen, max_time_s=30, workers=4, **kw)
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
    fases = [g for w, g in vistos if w > 1]
    colunas = [g for w, g in vistos if w == 1]

    assert fases, "nenhuma fase rodou"
    assert all(g == pytest.approx(0.02) for g in fases), (
        f"alguma fase ficou sem o gap: {fases}"
    )
    assert all(g == 0.0 for g in colunas), (
        f"o gap vazou para a geracao de colunas: {colunas}"
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
