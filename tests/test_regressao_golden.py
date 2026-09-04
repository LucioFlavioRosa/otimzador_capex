"""Regressao golden — trava os numeros atuais do banco de teste CTS. Se uma mudanca futura
alterar o resultado (de proposito ou por engano), estes testes acusam. Para atualizar de
proposito, rode `python tests/atualiza_golden.py` e revise o diff.

Como avaliar (build-all) ignora o teto, VPL/CAPEX/cobertura sao deterministicos e independem
do orcamento — por isso o golden e travado no build-all. O SOLVER maximiza o VPL, entao fica
>= build-all (nunca igual por obrigacao); para ele checamos o invariante de otimalidade, nao um
numero fixo (que variaria entre versoes de OR-Tools)."""
import pytest
from _helpers import load_cts, build_all, capex_total, cobertura_fim, silent, solver_or_skip

# valores de referencia (banco_teste_CTS_poc_v2) — congelados em 2026-07
GOLDEN = {
    True:  dict(vpl=107303304.663241, capex=6476000.0, cobertura=4800.0,
                universo=5100.0, vazao=430.0, obras=28, n_cts=2),
    # O CENARIO DESLIGADO MUDOU EM 14/08/2026, e a mudanca e intencional. A linha da CTS
    # deixou de ser somada na sub-bacia: a unica diferenca entre ligado e desligado passou
    # a ser QUAL COLUNA E LIDA — a exclusiva ou a `*_com_cts`. Nada e somado, nada e
    # ponderado, nada e derivado.
    #
    #   universo   5100 -> 3900    esta fixture NAO tem as colunas `*_com_cts`, entao o
    #   cobertura  4800 -> 3900    desligado usa o universo EXCLUSIVO das sub-bacias e a
    #                              area do coletor fica sem atendimento (o motor ALERTA)
    #   vazao       430 -> 340     a vazao da CTS nao e herdada: e dado da sub-bacia, e
    #                              quem atualiza a base para esse cenario e quem cadastra
    #   vpl     107,72 -> 82,62 Mi menos gente ligada e sem a receita da linha da CTS
    #
    # NAO mudaram: capex (5.640.000) e obras (20) — as obras da CTS ja ficavam de fora.
    #
    # Base COM as colunas consolidadas nao perde a area sobreposta; e o caso que
    # `test_cts.py::test_a_area_so_do_coletor_nao_e_atendida_sem_ele` cobre. Esta fixture
    # exercita de proposito o caminho da base ainda nao atualizada.
    False: dict(vpl=82624810.346347, capex=5640000.0, cobertura=3900.0,
                universo=3900.0, vazao=340.0, obras=20, n_cts=0),
}


@pytest.mark.parametrize("usar_cts", [True, False], ids=["ligado", "desligado"])
def test_golden_build_all(usar_cts):
    g = GOLDEN[usar_cts]
    cen = load_cts(usar_cts)
    res = build_all(cen)
    assert res["vpl"] == pytest.approx(g["vpl"], rel=1e-6)
    assert capex_total(cen, res) == pytest.approx(g["capex"], rel=1e-6)
    assert cobertura_fim(res) == pytest.approx(g["cobertura"])
    assert sum(cen.max_lig.values()) == pytest.approx(g["universo"])
    assert sum(cen.vazao.values()) == pytest.approx(g["vazao"])
    assert sum(1 for o in cen.obras.values() if o.eh_aegea()) == g["obras"]
    assert len(cen.cts_ids) == g["n_cts"]


@pytest.mark.solver
@pytest.mark.parametrize("usar_cts", [True, False], ids=["ligado", "desligado"])
def test_solver_otimo_fica_acima_do_golden_build_all(usar_cts):
    # o solver otimiza -> VPL >= o golden do build-all (piso), e nao absurdamente acima.
    CP = solver_or_skip()
    g = GOLDEN[usar_cts]
    cen = load_cts(usar_cts)
    res = silent(CP.resolver_por_sistema, cen, max_time_s=60, workers=4)
    assert res["vpl"] >= g["vpl"] - 1.0, "solver abaixo do build-all (nao deveria)"
    assert res["vpl"] <= g["vpl"] * 1.10, "VPL muito acima do build-all — investigar"
