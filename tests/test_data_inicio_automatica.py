"""A DATA DE INICIO DAS OBRAS E AUTOMATICA quando ninguem a informa.

Regra (09/2026): se o primeiro ano do CAPEX e o ano da rodada, as obras comecam no
mes seguinte ao da rodada; se o CAPEX comeca num ano futuro, comecam em janeiro
dele. Rodada em 14/09/2026 com CAPEX a partir de 2026 -> 10/2026; CAPEX a partir
de 2027 -> 01/2027.

O `conftest` fixa o "hoje" dos outros testes em 2025 para o golden nao mudar com o
calendario; aqui o dia e o do exemplo da regra.
"""
from datetime import date

from _helpers import banco, BANK_FIXTURE, engine, silent

M = engine()
RODADA = date(2026, 9, 14)


def test_capex_no_ano_da_rodada_comeca_no_mes_seguinte():
    assert M.data_inicio_automatica(2026, RODADA) == (10, 2026)


def test_capex_em_ano_futuro_comeca_em_janeiro_dele():
    assert M.data_inicio_automatica(2027, RODADA) == (1, 2027)
    assert M.data_inicio_automatica(2030, RODADA) == (1, 2030)


def test_capex_em_ano_ja_passado_tambem_comeca_no_mes_seguinte():
    """O cronograma padrao da tela comeca em 2026 e vai continuar comecando ate alguem
    o mudar: rodado em 2027, nao pode por obra em meses que ja acabaram."""
    assert M.data_inicio_automatica(2026, date(2027, 3, 5)) == (4, 2027)


def test_dezembro_vira_janeiro_do_ano_seguinte():
    assert M.data_inicio_automatica(2026, date(2026, 12, 20)) == (1, 2027)


def test_sem_dia_informado_usa_o_hoje_do_motor(monkeypatch):
    monkeypatch.setattr(M, "hoje", lambda: RODADA)
    assert M.data_inicio_automatica(2026) == (10, 2026)


def _ler(orc, monkeypatch, **k):
    monkeypatch.setattr(M, "hoje", lambda: RODADA)
    return silent(M.ler_banco, banco(BANK_FIXTURE), orcamento=orc, unidade="u1", usar_cts=False, **k)


def test_ler_banco_sem_data_inicio_aplica_a_regra(monkeypatch):
    """`mes_inicio` e o indice interno 0-based a partir de janeiro do ano-base (2026 na
    fixture): outubro de 2026 e o mes 9; janeiro de 2027 e o mes 12."""
    cen = _ler({2026: 50e6, 2027: 50e6}, monkeypatch)
    assert cen.mes_inicio == 9
    assert all(o.inicio_min >= 9 for o in cen.obras.values())

    cen = _ler({2027: 50e6, 2028: 50e6}, monkeypatch)
    assert cen.mes_inicio == 12


def test_data_inicio_informada_continua_valendo(monkeypatch):
    """A regra so preenche o que ficou vazio: quem manda `data_inicio` manda."""
    cen = _ler({2026: 50e6, 2027: 50e6}, monkeypatch, data_inicio=(3, 2027))
    assert cen.mes_inicio == 14


def test_teto_anual_unico_conta_do_ano_base(monkeypatch):
    """Sem cronograma a janela comeca no ano-base do cadastro (2026 na fixture) — e
    e ele o "primeiro ano do CAPEX" da regra."""
    cen = _ler(50e6, monkeypatch, horizonte_capex=4)
    assert cen.mes_inicio == 9


def test_inicio_depois_da_janela_de_capex_e_erro_e_nao_plano_vazio(monkeypatch):
    """Cronograma so de 2026 rodado em dezembro de 2026: a data automatica e 01/2027, e a
    janela acabou em 2026 — nenhuma obra teria mes possivel. O motor diz isso em vez de
    devolver um plano vazio sem explicacao."""
    import pytest

    monkeypatch.setattr(M, "hoje", lambda: date(2026, 12, 20))
    with pytest.raises(ValueError, match="janela de CAPEX"):
        silent(M.ler_banco, banco(BANK_FIXTURE), orcamento={2026: 50e6}, unidade="u1", usar_cts=False)
    # Informada, a mesma data fora da janela e o mesmo erro.
    with pytest.raises(ValueError, match="janela de CAPEX"):
        silent(M.ler_banco, banco(BANK_FIXTURE), orcamento={2026: 50e6}, unidade="u1", usar_cts=False,
               data_inicio=(1, 2027))


def test_no_job_databricks_data_inicio_vazia_e_o_mesmo_que_ausente():
    """A tela manda `""` quando ninguem digitou; repassada ao motor ela morreria no
    parser de texto. Vazia ou ausente, e a data automatica que vale."""
    from otimizador.aplicacao import job_databricks as J

    assert "data_inicio" not in J._params_para_ler_banco({"ORCAMENTO": 1.0, "DATA_INICIO": ""})
    assert "data_inicio" not in J._params_para_ler_banco({"ORCAMENTO": 1.0, "DATA_INICIO": "  "})
    assert "data_inicio" not in J._params_para_ler_banco({"ORCAMENTO": 1.0, "DATA_INICIO": None})
    assert J._params_para_ler_banco({"ORCAMENTO": 1.0, "DATA_INICIO": "03-2027"})["data_inicio"] == "03-2027"
