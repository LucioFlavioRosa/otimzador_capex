"""A RECEITA DO KPI TEM DE FECHAR COM A SOMA DA SÉRIE ANUAL — E SÓ CONTA LIGAÇÃO NOVA.

Duas regras, e a segunda mudou de lado no mesmo dia:

1. **Os dois caminhos de leitura dão o mesmo número.** Defeito relatado por uma
   usuária em 28/09/2026 (ela somou a receita e não bateu com a simulação):
   `run_ano.receita_total` somava duas parcelas e `run_meta.receita_total` — o KPI
   "Receita" — somava uma. Numa rodada real de 24 anos a diferença era de R$ 245,5
   milhões (10,8%), e o EBITDA total herdava a mesma diferença porque sai de
   `receita_total − opex_total`.

2. **A receita é só a das ligações novas** (decisão do dono do produto, 28/09/2026). O
   EFEITO-BASE — a base já atendida passando a pagar a nova equivalência quando a
   cobertura da cidade sobe de faixa — é receita que apareceria sem o plano, e saiu do
   VPL, do EBITDA e da receita. Continua CALCULADO e publicado em
   `run_ano.receita_efeito_base`, para se ver o que ficou de fora.

Então a reconciliação da regra 1 agora é sobre a primeira parcela, e a regra 2 é o que
impede o total de voltar a somar a segunda. Nenhum dos testes fixa VALOR: eles fixam que
os dois caminhos concordam, e sobre o quê.
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


@pytest.fixture(scope="module")
def resultado_do_plano():
    """O `avaliar` do mesmo plano da fixture `tabelas` — para comparar o publicado com o
    calculado sem depender de o teste refazer a materialização."""
    return build_all(load_cts(True))      # o MESMO plano da fixture `tabelas`


def test_o_total_do_kpi_e_a_soma_da_serie_anual(tabelas):
    meta = tabelas["run_meta"].iloc[0]
    assert meta["receita_total"] == pytest.approx(tabelas["run_ano"]["receita_total"].sum())


def test_O_TOTAL_E_SO_AS_LIGACOES_NOVAS(tabelas):
    """O efeito-base fica FORA do total, e visível ao lado.

    Este teste era o inverso até 28/09/2026 (cobrava que o total somasse as duas). A
    inversão é a decisão do dono do produto, e o teste continua valendo a pena no novo
    sentido: sem ele, alguém que leia `receita_efeito_base` na tabela e a some ao total
    reporia no número o que o VPL deixou de contar.
    """
    ano = tabelas["run_ano"]
    meta = tabelas["run_meta"].iloc[0]
    novas = ano["receita"].sum()
    efeito = ano["receita_efeito_base"].sum()
    assert efeito > 0, "a fixture tem de ter efeito-base, senão o teste não prova nada"
    assert meta["receita_total"] == pytest.approx(novas)
    assert meta["receita_total"] < novas + efeito, "o efeito-base voltou para o total"


def test_A_COLUNA_QUE_O_BACKEND_SUBTRAI_E_ZERO(tabelas):
    """`vp_efeito_base` publicado é ZERO, e isso é o contrato com o backend.

    A coluna quer dizer "quanto do `vpl` ao lado é efeito-base", e é por isso que o
    backend a subtrai para mostrar o VPL do produto. Como o motor não soma mais o efeito
    ao `vpl`, a parcela a subtrair é zero — publicar o valor calculado faria o backend
    descontar duas vezes, e o VPL na tela ficaria menor que o real.

    O NOME DIZ SÓ O QUE O TESTE PROVA. A versão anterior se chamava "o VPL publicado não
    tem efeito-base a subtrair", e isso ele não verificava: prometia uma afirmação sobre o
    `vpl` e checava duas colunas. Quem prova a outra metade é o teste seguinte.
    """
    meta = tabelas["run_meta"].iloc[0]
    assert meta["vp_efeito_base"] == 0.0
    sb = tabelas["run_subbacia"]
    assert (sb["vp_efeito_base"] == 0.0).all()


def test_O_VPL_PUBLICADO_NAO_CONTEM_O_EFEITO(tabelas, resultado_do_plano):
    """O `vpl` publicado é a soma das QUATRO parcelas — sem a do efeito-base.

    A primeira versão deste teste comparava o publicado com `res["vpl"]` e afirmava que
    ele era menor que `res["vpl"] + efeito`. As duas coisas eram verdade no código ANTIGO
    também (lá o publicado também era igual ao calculado; o problema é que os dois
    incluíam o efeito), então ele não distinguia regra nova de velha — a segunda revisão
    do Codex apontou. Comparar com a DECOMPOSIÇÃO é o que distingue: ela é a única forma
    de dizer "o total não tem esta parcela".
    """
    meta = tabelas["run_meta"].iloc[0]
    res = resultado_do_plano
    assert res["vp_efeito_base"] > 0, "a fixture tem de ter efeito-base, senão nada é provado"

    dec = engine().vpl_por_subbacia(load_cts(True), res)
    T = {k: sum(d[k] for d in dec.values())
         for k in ("capex", "opex", "rec_dir", "rec_ind", "efeito_base")}
    quatro = T["rec_dir"] + T["rec_ind"] + T["capex"] + T["opex"]

    assert meta["vpl"] == pytest.approx(quatro), "o publicado não é a soma das quatro"
    assert meta["vpl"] == pytest.approx(res["vpl"])
    # E a prova de que a regra é a NOVA: somar a quinta parcela dá outro número, e é esse
    # que o código antigo publicava.
    assert meta["vpl"] != pytest.approx(quatro + T["efeito_base"])
    assert T["efeito_base"] == pytest.approx(res["vp_efeito_base"])


def test_O_EBITDA_POR_SUBBACIA_E_ANO_TAMBEM_EXCLUI_O_EFEITO(tabelas):
    """A coluna `ebitda` de `run_subbacia_ano` seguia somando o efeito-base.

    Achado pela revisão do Codex em 29/09/2026: o backend se protegia recalculando
    (`receita_direta + receita_indireta - opex_rateado`), então a tela estava certa, mas a
    coluna gravada ficava R$ 67.716.132,01 acima numa rodada real — e o leitor offline,
    que soma a coluna, reintroduzia o efeito depois de ele ter saído de todo o resto.
    """
    sa = tabelas["run_subbacia_ano"]
    assert not sa.empty
    assert sa["efeito_base"].sum() > 0, "a fixture tem de ter efeito-base"
    esperado = sa["receita_direta"] + sa["receita_indireta"] - sa["opex_rateado"]
    assert sa["ebitda"].tolist() == pytest.approx(esperado.tolist())


def test_o_ebitda_total_fecha_com_a_soma_anual(tabelas):
    """O EBITDA total da tela é `receita_total − opex_total`. Se a receita do KPI
    tem outra definição que a da série, esta subtração erra pelo mesmo tanto."""
    meta = tabelas["run_meta"].iloc[0]
    do_kpi = meta["receita_total"] - meta["opex_total"]
    assert do_kpi == pytest.approx(tabelas["run_ano"]["ebitda"].sum())
