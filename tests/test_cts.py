"""CTS (Coletor de Tempo Seco) — as duas visoes (usar_cts ligado x desligado) sao a MESMA
demanda. O que TEM de bater: cobertura, vazao, universo efetivo. O que TEM de diferir de
proposito: numero de obras, CAPEX e VPL. Estes testes travam esse contrato."""
import pytest
from _helpers import engine, load_fixture, capex_total, cobertura_fim, codigo

CTS_COMPONENTES = {"cts", "tro", "eee", "lr"}   # Coletor de tempo seco + Tronco + EEE + Linha de recalque


# ---------------------------------------------------------------- estrutura
def test_cts_entram_como_nos_no_modo_ligado(cen_on, cen_off):
    assert cen_on.cts_ids, "modo ligado deveria ter CTS carregadas"
    assert not cen_off.cts_ids, "modo desligado nao deve ter nenhuma CTS"
    assert all(cen_on.nos[c].is_cts for c in cen_on.cts_ids)
    assert not any(getattr(n, "is_cts", False) for n in cen_off.nos.values())


def test_cada_cts_tem_os_quatro_componentes_certos(cen_on):
    for c in cen_on.cts_ids:
        obras = [o for o in cen_on.obras.values() if o.no == c and o.eh_aegea()]
        cods = {codigo(o.id) for o in obras}
        assert cods == CTS_COMPONENTES, f"CTS {c}: esperado {CTS_COMPONENTES}, veio {cods}"
        coletas = [o for o in obras if o.tipo == "coleta"]
        assert len(coletas) == 1 and codigo(coletas[0].id) == "cts", \
            "a ancora de coleta da CTS deve ser o Coletor de tempo seco"


# ---------------------------------------------------------------- invariantes (TEM de bater)
def test_invariante_cobertura_identica(res_on, res_off):
    assert cobertura_fim(res_on) == pytest.approx(cobertura_fim(res_off))


def test_invariante_vazao_identica(cen_on, cen_off):
    assert sum(cen_on.vazao.values()) == pytest.approx(sum(cen_off.vazao.values()))


def test_invariante_universo_efetivo_identico(cen_on, cen_off):
    # universo efetivo = universo_ligacoes x potencial (max_lig). A media ponderada do
    # potencial no modo desligado tem de preservar exatamente esse total.
    assert sum(cen_on.max_lig.values()) == pytest.approx(sum(cen_off.max_lig.values()))


# ---------------------------------------------------------------- diferencas (TEM de diferir)
def test_ligado_tem_quatro_obras_a_mais_por_cts(cen_on, cen_off):
    n_on = sum(1 for o in cen_on.obras.values() if o.eh_aegea())
    n_off = sum(1 for o in cen_off.obras.values() if o.eh_aegea())
    assert n_on - n_off == 4 * len(cen_on.cts_ids)


def test_capex_ligado_maior_e_diferenca_e_o_capex_das_obras_cts(cen_on, cen_off, res_on, res_off):
    cap_on = capex_total(cen_on, res_on)
    cap_off = capex_total(cen_off, res_off)
    assert cap_on > cap_off, "o modo ligado paga as obras dedicadas da CTS"
    capex_cts = sum(o.capex for o in cen_on.obras.values()
                    if getattr(cen_on.nos.get(o.no), "is_cts", False))
    assert cap_on - cap_off == pytest.approx(capex_cts), \
        "a diferenca de CAPEX tem de ser exatamente o CAPEX das obras da CTS"


def test_vpl_desligado_maior(res_on, res_off):
    # mesma receita/cobertura com menos CAPEX => desligado tem VPL maior.
    assert res_off["vpl"] > res_on["vpl"]


# ---------------------------------------------------------------- retrocompatibilidade
def test_banco_sem_cts_modos_sao_identicos():
    # base fixa SEM CTS -> usar_cts ligado ou desligado tem de dar exatamente o mesmo
    a = load_fixture(usar_cts=True)
    b = load_fixture(usar_cts=False)
    assert not a.cts_ids and not b.cts_ids, "o fixture nao deve ter CTS"
    assert set(a.nos) == set(b.nos)
    assert set(a.obras) == set(b.obras)


# ---------------------------------------------------------------- sobreposicao consolidada
#
# AS INVARIANTES DE CIMA VALEM NO CAMINHO DE COMPATIBILIDADE, e este bloco existe para
# dizer por que. Enquanto a origem nao traz as colunas `*_com_cts`, o motor SOMA a linha
# da CTS na sub-bacia — e somar conserva tudo, entao ligado e desligado tem a mesma
# demanda total. E tambem CONTA DUAS VEZES a area sobreposta, que e o defeito.
#
# Com as colunas consolidadas, a igualdade deixa de valer POR CONSTRUCAO: sem o coletor,
# a parte da area que so ele alcancava nao e atendida por ninguem. O que se conserva e
# outra coisa — cada cenario conta a sobreposicao uma vez.
import shutil

import openpyxl
from _helpers import BANK_CTS, silent


def _com_consolidado(tmp_path, sub, valores):
    """Copia o banco de CTS e grava as colunas `*_com_cts` na sub-bacia `sub`."""
    dst = tmp_path / "cts_consolidado.xlsx"
    shutil.copy(BANK_CTS, dst)
    wb = openpyxl.load_workbook(dst)
    ws = wb["subbacia-operacional"]
    cab = [str(c.value).strip() for c in ws[1]]
    ichave = cab.index("sub_bacia") + 1
    for col, val in valores.items():
        if col in cab:
            icol = cab.index(col) + 1
        else:
            icol = ws.max_column + 1
            ws.cell(1, icol).value = col
            cab.append(col)
        for r in range(2, ws.max_row + 1):
            if ws.cell(r, ichave).value == sub:
                ws.cell(r, icol).value = val
    wb.save(dst)
    return str(dst)


def test_sem_cts_le_a_coluna_consolidada_em_vez_de_somar(tmp_path):
    # b1 tem 1000 ligacoes exclusivas e a cts1 tem 500. A soma daria 1500 — e conta a
    # area sobreposta duas vezes. A coluna diz que a sub-bacia, sem o coletor, atende
    # 1200: as 1000 dela mais 200 de sobreposicao.
    arq = _com_consolidado(tmp_path, "b1", {
        "universo_ligacoes_com_cts": 1200,
        "ligacoes_atuais_com_cts": 450,
        "universo_economias_com_cts": 1320,
        "economias_atuais_com_cts": 495,
    })
    M = engine()
    off = silent(M.ler_banco, arq, usar_cts=False)
    cid = off.nos["b1"].cidade
    # b1 e b2 estao na mesma cidade. O universo EFETIVO ja leva o potencial:
    #   sobreposicao = 1200 - 1000 = 200, com o potencial da CTS (1,2)
    #   potencial de b1 = (1000x1,0 + 200x1,2) / 1200 = 1,03333
    #   b1 = 1200 x 1,03333 = 1240   (+ b2 = 900, potencial 1,0)
    # Somando as duas linhas daria 1600 em b1 — a area sobreposta contada duas vezes.
    assert off.max_lig[cid] == pytest.approx(2140.0)


def test_a_area_so_do_coletor_nao_e_atendida_sem_ele(tmp_path):
    # O que deixa de valer de proposito: ligado e desligado NAO tem mais a mesma demanda.
    # Sem o coletor, a parte da area que so ele alcancava (500 - 200 = 300) fica de fora.
    arq = _com_consolidado(tmp_path, "b1", {
        "universo_ligacoes_com_cts": 1200,
        "ligacoes_atuais_com_cts": 450,
        "universo_economias_com_cts": 1320,
        "economias_atuais_com_cts": 495,
    })
    M = engine()
    on = silent(M.ler_banco, arq, usar_cts=True)
    off = silent(M.ler_banco, arq, usar_cts=False)
    assert sum(off.max_lig.values()) < sum(on.max_lig.values())
    # 300 ligacoes que so o coletor alcancava, com o potencial dele (1,2) = 360 de
    # universo efetivo.
    assert sum(on.max_lig.values()) - sum(off.max_lig.values()) == pytest.approx(360.0)


def test_sem_a_coluna_volta_a_somar_e_avisa(tmp_path, capsys):
    # Degradacao honesta: perder a demanda do coletor seria pior que conta-la duas vezes,
    # e o aviso diz qual dos dois comportamentos a rodada teve.
    M = engine()
    with capsys.disabled() if False else __import__("contextlib").nullcontext():
        off = M.ler_banco(BANK_CTS, usar_cts=False)
    saida = capsys.readouterr().out
    assert "a demanda foi SOMADA" in saida
    cid = off.nos["b1"].cidade
    # 1000 + 500x1,2 (cts1 somada, com o potencial dela) + 900 = 2500 — exatamente o
    # mesmo total do modo ligado, que e a invariante do caminho de compatibilidade.
    assert off.max_lig[cid] == pytest.approx(2500.0)


def test_com_cts_ligada_a_coluna_consolidada_e_ignorada(tmp_path):
    # Ela so descreve o cenario SEM coletor. Com ele, a sub-bacia usa o que e exclusivo
    # dela e a CTS entra como no proprio — a sobreposicao esta nos numeros da CTS.
    arq = _com_consolidado(tmp_path, "b1", {
        "universo_ligacoes_com_cts": 1200,
        "ligacoes_atuais_com_cts": 450,
        "universo_economias_com_cts": 1320,
        "economias_atuais_com_cts": 495,
    })
    M = engine()
    on_com = silent(M.ler_banco, arq, usar_cts=True)
    on_sem = silent(M.ler_banco, BANK_CTS, usar_cts=True)
    assert sum(on_com.max_lig.values()) == pytest.approx(sum(on_sem.max_lig.values()))
