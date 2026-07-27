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
