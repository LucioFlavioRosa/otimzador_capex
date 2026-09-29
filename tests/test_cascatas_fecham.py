"""AS CASCATAS TÊM DE FECHAR NO VPL, E OS GRÁFICOS TÊM DE DESENHAR.

Nasceu de dois defeitos do mesmo dia (28-29/09/2026), quando o efeito-base saiu da conta:

1. **A cascata somava uma parcela que o total não tem.** `receita direta + receita
   indireta + efeito-base + CAPEX + OPEX` era desenhado com a soma rotulada "VPL" — e o
   VPL deixou de incluir o efeito. A barra final passaria a mostrar VPL + efeito-base, um
   número que não existe em lugar nenhum.

2. **O gráfico da sub-bacia QUEBROU.** Ao tirar o efeito da lista de itens, os rótulos
   ficaram numa lista paralela escrita à mão com um a mais, e `set_xticklabels` recebia
   mais rótulo que tick — `deep_dive_subbacia(..., grafico=True)` levantava erro. Nada na
   suíte exercitava `grafico=True`, e é por isso que passou.

Estes testes cobrem as duas coisas: a identidade aritmética e o fato de os gráficos
desenharem. Não fixam valor — fixam que as parcelas somam o total e que o código roda.
"""
import pytest

from _helpers import build_all, engine, load_cts


@pytest.fixture(scope="module")
def cenario():
    cen = load_cts(True)
    return cen, build_all(cen)


def test_as_parcelas_somam_o_vpl_sem_o_efeito_base(cenario):
    """A conta da cascata: as quatro parcelas dão o VPL, e o efeito fica fora."""
    cen, res = cenario
    dec = engine().vpl_por_subbacia(cen, res)
    T = {k: sum(d[k] for d in dec.values())
         for k in ("capex", "opex", "rec_dir", "rec_ind", "efeito_base", "vpl")}

    assert T["efeito_base"] > 0, "a fixture tem de ter efeito-base, senão nada é provado"
    quatro = T["rec_dir"] + T["rec_ind"] + T["capex"] + T["opex"]
    assert quatro == pytest.approx(T["vpl"]), "a cascata não fecha no VPL"
    assert quatro == pytest.approx(res["vpl"]), "o VPL por sub-bacia difere do de `avaliar`"
    # E somar o efeito daria OUTRO número — o que a barra final mostrava antes.
    assert quatro + T["efeito_base"] != pytest.approx(T["vpl"])


def test_os_graficos_do_dashboard_desenham(cenario):
    """`grafico=True` não era exercitado por nada, e foi assim que a quebra passou."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    import matplotlib

    matplotlib.use("Agg")                      # sem janela: só o desenho tem de sair
    import matplotlib.pyplot as plt

    from otimizador.apresentacao import dashboard_otimizador_v2 as D

    cen, res = cenario
    D.set_engine(engine())
    try:
        # As duas que desenham cascata do VPL, e eram as duas em risco: `painel_geral`
        # tinha a parcela somada no total, `deep_dive_subbacia` tinha os rótulos numa
        # lista paralela que ficou com um a mais.
        D.painel_geral(cen, res)
        D.deep_dive_subbacia(cen, res, sorted(cen.nos)[0], grafico=True)
    finally:
        plt.close("all")
