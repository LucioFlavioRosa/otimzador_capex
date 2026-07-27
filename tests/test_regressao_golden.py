"""Regressao golden — trava os numeros atuais do banco de teste CTS. Se uma mudanca futura
alterar o resultado (de proposito ou por engano), estes testes acusam. Para atualizar de
proposito, rode `python tests/atualiza_golden.py` e revise o diff.

Como avaliar (build-all) ignora o teto, VPL/CAPEX/cobertura sao deterministicos e independem
do orcamento — por isso o golden e travado no build-all. O SOLVER maximiza o VPL, entao fica
>= build-all (nunca igual por obrigacao); para ele checamos o invariante de otimalidade, nao um
numero fixo (que variaria entre versoes de OR-Tools)."""
import pytest
from _helpers import load_cts, build_all, capex_total, cobertura_fim, silent, solver_or_skip

# valores de referencia (banco_teste_CTS_poc_v2.xlsx) — congelados em 2026-07
GOLDEN = {
    True:  dict(vpl=107303304.663241, capex=6476000.0, cobertura=4800.0,
                universo=5100.0, vazao=430.0, obras=28, n_cts=2),
    False: dict(vpl=107717807.340670, capex=5640000.0, cobertura=4800.0,
                universo=5100.0, vazao=430.0, obras=20, n_cts=0),
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
