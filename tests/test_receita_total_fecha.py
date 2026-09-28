"""A RECEITA DO KPI TEM DE FECHAR COM A SOMA DA SÉRIE ANUAL.

Defeito relatado por uma usuária em 28/09/2026 (ela somou a receita e não
bateu com a simulação). A receita do plano tem duas parcelas:

  `receita_ano`       as ligações NOVAS que as obras habilitam;
  `efeito_base_ano`   o EFEITO-BASE — a base já atendida passa a pagar a nova
                      equivalência quando a cobertura da cidade sobe de faixa.

`run_ano.receita_total` somava as duas; `run_meta.receita_total` — o que a tela
mostra no KPI "Receita" — somava só a primeira. Numa rodada real de 24 anos a
diferença era de R$ 245,5 milhões (10,8%), e o EBITDA total herdava a mesma
diferença porque sai de `receita_total − opex_total`.

Estes testes travam a reconciliação. Eles NÃO fixam um valor: fixam que os dois
caminhos de leitura do mesmo conceito dão o mesmo número, que é o que a usuária
conferiu à mão.
"""
import pytest

from _helpers import build_all, engine, load_cts, silent


@pytest.fixture(scope="module")
def tabelas():
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.infraestrutura import persistencia as P
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen = load_cts(True)
    return silent(P.materializar, cen, build_all(cen), run_id="run_receita", banco="pg")


def test_o_total_do_kpi_e_a_soma_da_serie_anual(tabelas):
    meta = tabelas["run_meta"].iloc[0]
    assert meta["receita_total"] == pytest.approx(tabelas["run_ano"]["receita_total"].sum())


def test_o_total_inclui_as_duas_parcelas(tabelas):
    ano = tabelas["run_ano"]
    meta = tabelas["run_meta"].iloc[0]
    novas = ano["receita"].sum()
    efeito = ano["receita_efeito_base"].sum()
    assert efeito > 0, "a fixture tem de ter efeito-base, senão o teste não prova nada"
    assert meta["receita_total"] == pytest.approx(novas + efeito)
    assert meta["receita_total"] > novas, "o total não pode ser só as ligações novas"


def test_o_ebitda_total_fecha_com_a_soma_anual(tabelas):
    """O EBITDA total da tela é `receita_total − opex_total`. Se a receita do KPI
    tem outra definição que a da série, esta subtração erra pelo mesmo tanto."""
    meta = tabelas["run_meta"].iloc[0]
    do_kpi = meta["receita_total"] - meta["opex_total"]
    assert do_kpi == pytest.approx(tabelas["run_ano"]["ebitda"].sum())
