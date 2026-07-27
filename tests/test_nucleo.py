"""Nucleo do otimizador — regras que precisam continuar valendo depois de qualquer mudanca:
perfil de OPEX, CAPEX = quantidade x preco, regra do WACC, janela de conclusao, leitura estrita
de nomes de coluna e (com solver) teto de orcamento e separabilidade por cidade."""
import shutil
import pytest
from _helpers import (engine, load_cts, load_fixture, build_all, capex_total,
                      silent, solver_or_skip, BANK_CTS, ORC_SLACK)


# ---------------------------------------------------------------- OPEX
def test_opex_profile_concavo_ate_a_maturacao():
    M = engine()
    assert M._opex_mult(0, 12) == pytest.approx(M._OPEX_FRAC_INICIAL)  # comeca no piso
    assert M._opex_mult(12, 12) == 1.0                                  # atinge o maximo na maturacao
    assert M._opex_mult(20, 12) == 1.0                                  # e nao ultrapassa
    assert M._opex_mult(3, 1) == 1.0                                    # mat<=1 => sempre no maximo
    assert M._OPEX_FRAC_INICIAL < 1.0                                   # pico inicial abaixo do plato
    vals = [M._opex_mult(m, 12) for m in range(0, 13)]
    difs = [b - a for a, b in zip(vals, vals[1:])]
    assert all(d >= -1e-9 for d in difs), "OPEX nao pode decrescer"
    assert all(difs[i + 1] <= difs[i] + 1e-9 for i in range(len(difs) - 1)), "deve ser concavo"


# ---------------------------------------------------------------- CAPEX
def test_capex_igual_quantidade_vezes_preco(cen_on):
    checados = 0
    for o in cen_on.obras.values():
        q = getattr(o, "quantidade", None)
        pu = getattr(o, "preco_unitario", None)
        if q is not None and pu is not None:
            assert o.capex == pytest.approx(q * pu, rel=1e-6), f"{o.id}: capex != q*pu"
            checados += 1
    assert checados > 0, "esperava componentes com quantidade e preco_unitario"


# ---------------------------------------------------------------- WACC (regra do Rossi)
def test_todo_elemento_aegea_tem_wacc_e_origem_rotulada(cen_on):
    for o in cen_on.obras.values():
        if not o.eh_aegea():
            continue
        assert o.wacc is not None, f"{o.id} ficou sem WACC"
        assert o.wacc_origem in {"proprio", "wacc_medio", "ausente"}


def test_wacc_vazio_consome_o_wacc_medio_da_unidade():
    # o fixture deixa ~60% dos WACC vazios (herdam o wacc_medio) e ~40% proprios
    cen = load_fixture(usar_cts=True)
    medio = [o for o in cen.obras.values() if o.eh_aegea() and o.wacc_origem == "wacc_medio"]
    proprio = [o for o in cen.obras.values() if o.eh_aegea() and o.wacc_origem == "proprio"]
    assert medio and proprio, "esperava um mix de WACC proprio e medio"
    valores = {round(o.wacc, 6) for o in medio}
    assert len(valores) == 1, "todos que herdam devem usar o MESMO wacc_medio da unidade"


# ---------------------------------------------------------------- janela de CAPEX
def test_anos_extra_conclusao_configura_a_cauda():
    M = engine()
    assert load_cts(True).anos_extra == 3, "default de anos_extra_conclusao deve ser 3"
    cen0 = silent(M.ler_banco, BANK_CTS, orcamento=ORC_SLACK, usar_cts=True, anos_extra_conclusao=0)
    assert cen0.anos_extra == 0
    assert cen0.orc_janela_total, "a sobra da janela (carry-forward) precisa estar disponivel"


# ---------------------------------------------------------------- leitura ESTRITA de nomes
def test_sem_fallback_para_nome_de_coluna_errado(tmp_path):
    import openpyxl
    M = engine()
    correto = sum(load_cts(True).vazao.values())
    dst = tmp_path / "quebrado.xlsx"
    shutil.copy(BANK_CTS, dst)
    wb = openpyxl.load_workbook(dst)
    ws = wb["subbacia-operacional"]
    for c in ws[1]:
        if c.value == "vazao_contribuicao":
            c.value = "vazao_marginal"      # nome ANTIGO/errado
    wb.save(dst)
    quebrado = sum(silent(M.ler_banco, str(dst), orcamento=ORC_SLACK, usar_cts=True).vazao.values())
    assert quebrado != pytest.approx(correto), \
        "o nome antigo NAO pode ser aceito como alias (regra: sem fallback)"


# ================================================================ SOLVER (OR-Tools)
@pytest.mark.solver
def test_solver_respeita_o_teto_anual():
    CP = solver_or_skip()
    M = engine()
    orc = {2026: 2e6, 2027: 2e6, 2028: 2e6, 2029: 2e6}     # apertado de proposito
    cen = silent(M.ler_banco, BANK_CTS, orcamento=orc, usar_cts=True)
    res = silent(CP.resolver_por_sistema, cen, max_time_s=60, workers=4)
    ok, viol = M.auditar_orcamento(cen, res)
    assert ok, f"o solver estourou o teto anual: {viol}"


@pytest.mark.solver
def test_solver_nunca_pior_que_o_build_all_e_cumpre_metas():
    # O solver MAXIMIZA o VPL sujeito a orcamento/dependencias. Com orcamento folgado, o
    # 'constroi tudo' (build_all) e um plano VIAVEL -> o otimo tem de ser >= a ele (nunca pior),
    # e normalmente MELHOR, porque o solver larga obra que destroi valor. Nao ha igualdade
    # obrigatoria. Alem disso, deve cumprir as metas de cobertura (deficit ~ 0).
    CP = solver_or_skip()
    cen = load_cts(True)
    res = silent(CP.resolver_por_sistema, cen, max_time_s=60, workers=4)
    ref = build_all(cen)
    assert res["vpl"] >= ref["vpl"] - 1.0, "o solver ficou pior que o build-all (nao deveria)"
    assert res["vpl"] <= ref["vpl"] * 1.10, "VPL muito acima do build-all — investigar (sanidade)"
    assert res.get("deficit_cobertura", 0.0) == pytest.approx(0.0, abs=1.0)


@pytest.mark.solver
@pytest.mark.slow
def test_separabilidade_por_cidade_e_exata():
    solver_or_skip()   # tambem instala os shims de nome
    # a suite legada `testes_otimizador.py` nao acompanha o pacote de producao; quando ela
    # nao esta na sessao, PULA (mesma politica de `require_bank` e `solver_or_skip`).
    TT = pytest.importorskip("testes_otimizador",
                             reason="suite legada `testes_otimizador.py` ausente neste pacote")
    cen = load_cts(True)
    assert TT.teste_separabilidade(cen), "a decomposicao por cidade deveria fechar (diff ~ 0)"
