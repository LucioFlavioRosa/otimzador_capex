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
#
# O VPL DOS DOIS CENARIOS MUDOU EM 28/09/2026, e a mudanca e intencional: o ticket passou
# a sair das ligacoes TOTAIS (`universo_ligacoes`) e nao das atuais — defeito relatado
# pelo dono do produto, ver `test_ticket_por_ligacoes_totais.py`. Na fixture o universo e
# 2,3x a 3,0x as atuais, e o ticket caiu na mesma proporcao:
#
#   ligado      vpl  107,30 -> 38,59 Mi
#   desligado   vpl   82,62 -> 29,36 Mi
#
# NADA MAIS MUDOU — capex, cobertura, universo, vazao, obras e n_cts estao iguais nos dois
# cenarios, e e essa a conferencia de que a correcao mexeu so na RECEITA.
#
# O VPL MUDOU DE NOVO EM 28/09/2026, tambem de proposito: O EFEITO-BASE SAIU DA CONTA
# (decisao do dono do produto — so entra receita de LIGACAO NOVA). A diferenca reconcilia
# AO CENTAVO com a parcela excluida, e e essa a prova de que nada mais mexeu:
#
#   ligado      38.591.020,99 = 33.053.288,92 + 5.537.732,06 de efeito-base
#   desligado   29.357.901,26 = 25.034.710,96 + 4.323.190,31 de efeito-base
#
# `vp_efeito_base` continua no retorno de `avaliar` (e e ele que fecha a conta acima), mas
# `vpl` nao o soma mais. Ver `test_receita_total_fecha.py`.
GOLDEN = {
    True:  dict(vpl=33053288.924913, capex=6476000.0, cobertura=4800.0,
                universo=5100.0, vazao=430.0, obras=28, n_cts=2),
    # O CENARIO DESLIGADO MUDOU EM 14/08/2026, e a mudanca e intencional. A linha da CTS
    # deixou de ser somada na sub-bacia: a unica diferenca entre ligado e desligado passou
    # a ser QUAL COLUNA E LIDA. Nada e somado, nada e ponderado, nada e derivado.
    #
    #   universo   5100 -> 3900    esta fixture NAO tem as colunas `*_com_cts`, entao os
    #   cobertura  4800 -> 3900    dois modos leem a sub-bacia INTEIRA; desligado, a
    #                              area do coletor fica sem o potencial dele e sem a CTS
    #   vazao       430 -> 340     a vazao da CTS nao e herdada: e dado da sub-bacia, e
    #                              quem atualiza a base para esse cenario e quem cadastra
    #   vpl     107,72 -> 82,62 Mi menos gente ligada e sem a receita da linha da CTS
    #
    # NAO mudaram: capex (5.640.000) e obras (20) — as obras da CTS ja ficavam de fora.
    #
    # EM 09/2026 A SEMANTICA DAS COLUNAS FOI VIRADA (a `*_com_cts` e a sub-bacia COM a CTS
    # a parte, lida no modo LIGADO), e os numeros daqui nao mudaram: sem as colunas na
    # fixture, o motor le a sub-bacia inteira nos dois modos — no ligado, ALERTANDO que a
    # area do coletor conta duas vezes. Base COM as colunas e o caso que
    # `test_cts.py::test_a_area_do_coletor_e_contada_uma_vez_em_cada_cenario` cobre.
    False: dict(vpl=25034710.958666, capex=5640000.0, cobertura=3900.0,
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
