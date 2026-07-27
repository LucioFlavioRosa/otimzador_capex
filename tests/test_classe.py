"""Classe da demanda: residencial x industrial (parametro incluir_industrial).

Regras que estes testes travam:
  - Banco SEM colunas *_industrial => os dois modos sao identicos (retrocompativel).
  - So residencial: CAPEX IGUAL (obras nao mudam); receita e vazao CAEM.
  - A queda de vazao e exatamente a parcela industrial.
  - Cobertura por LIGACOES e por ECONOMIAS cai; por POPULACAO fica intacta (industria ~ 0 habitantes).
"""
import shutil
import openpyxl
import pytest
from _helpers import (engine, silent, load_classe, load_fixture, build_all,
                      capex_total, BANK_CLASSE, BANK_FIXTURE)


def _cidade(cen, sb):
    return cen.nos[sb].cidade


# ---------------------------------------------------------------- retrocompat
def test_sem_colunas_industrial_modos_identicos():
    M = engine()
    a = silent(M.ler_banco, BANK_FIXTURE, unidade="u1", incluir_industrial=True)
    b = silent(M.ler_banco, BANK_FIXTURE, unidade="u1", incluir_industrial=False)
    assert set(a.obras) == set(b.obras)
    assert sum(a.vazao.values()) == pytest.approx(sum(b.vazao.values()))
    assert sum(a.max_lig.values()) == pytest.approx(sum(b.max_lig.values()))


# ---------------------------------------------------------------- CAPEX / vazao / receita
def test_capex_igual_entre_classes():
    on = load_classe(True); off = load_classe(False)
    assert capex_total(on, build_all(on)) == pytest.approx(capex_total(off, build_all(off)))


def test_so_residencial_reduz_vazao_no_exato_industrial():
    on = load_classe(True); off = load_classe(False)
    dif = sum(on.vazao.values()) - sum(off.vazao.values())
    assert dif == pytest.approx(35.0)   # 20 (b1) + 15 (b3) de vazao industrial


def test_so_residencial_reduz_receita():
    on = load_classe(True); off = load_classe(False)
    assert sum(build_all(off).get("receita_ano", [])) < sum(build_all(on).get("receita_ano", []))


# ---------------------------------------------------------------- cobertura por unidade
def test_cobertura_economias_cai():
    on = load_classe(True); off = load_classe(False)
    c1 = _cidade(on, "b1")   # cidade que mede em ECONOMIAS
    assert off.max_lig[c1] < on.max_lig[c1] - 1


def test_cobertura_populacao_intacta():
    on = load_classe(True); off = load_classe(False)
    c2 = _cidade(on, "b3")   # cidade que mede em POPULACAO
    assert off.max_lig[c2] == pytest.approx(on.max_lig[c2])


def test_cobertura_ligacoes_cai(tmp_path):
    # muda c1 para LIGACOES e confirma que o universo de cobertura cai no so-residencial
    dst = tmp_path / "classe_ligacoes.xlsx"
    shutil.copy(BANK_CLASSE, dst)
    wb = openpyxl.load_workbook(dst); ws = wb["cidade-operacional"]
    h = [c.value for c in ws[1]]; ic = h.index("cidade_id") + 1; iu = h.index("unidade_cobertura") + 1
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, ic).value == "c1":
            ws.cell(r, iu).value = "ligacoes"
    wb.save(dst)
    M = engine()
    on = silent(M.ler_banco, str(dst), incluir_industrial=True)
    off = silent(M.ler_banco, str(dst), incluir_industrial=False)
    c1 = on.nos["b1"].cidade
    assert off.max_lig[c1] < on.max_lig[c1] - 1
