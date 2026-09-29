"""Recalcula os numeros golden do banco de teste CTS e imprime o bloco GOLDEN pronto para colar
em test_regressao_golden.py. Use APENAS quando a mudanca de resultado for intencional:

    python tests/atualiza_golden.py

Revise o diff antes de colar — e justamente essa revisao que garante que nenhuma regressao passe
despercebida."""
from datetime import date

from _helpers import load_cts, build_all, capex_total, cobertura_fim, engine

#: O MESMO "HOJE" DO `conftest.py` (`HOJE_DOS_TESTES`), e sem isto o gerador mentia.
#:
#: Sem `data_inicio` no cadastro, o motor deriva o inicio das obras do primeiro ano de
#: CAPEX e do DIA DA RODADA. O `conftest` fixa `hoje()` em 2025-01-01 para o cronograma
#: cair em janeiro do primeiro ano; este script rodava com a data real, media outro
#: cronograma e imprimia um VPL R$ 387.450,00 diferente do que os testes comparam.
#:
#: Quem seguisse a instrucao do docstring colava um golden que falhava na hora — e podia
#: concluir que a mudanca dele quebrou mais coisa do que quebrou. Achado em 28/09/2026,
#: ao atualizar o golden pela exclusao do efeito-base.
HOJE_DOS_TESTES = date(2025, 1, 1)
engine().hoje = lambda: HOJE_DOS_TESTES


def medir(usar_cts):
    cen = load_cts(usar_cts)
    res = build_all(cen)
    return dict(
        vpl=round(res["vpl"], 6),
        capex=round(capex_total(cen, res), 6),
        cobertura=round(cobertura_fim(res), 6),
        universo=round(sum(cen.max_lig.values()), 6),
        vazao=round(sum(cen.vazao.values()), 6),
        obras=sum(1 for o in cen.obras.values() if o.eh_aegea()),
        n_cts=len(cen.cts_ids),
    )


if __name__ == "__main__":
    print("GOLDEN = {")
    for uc in (True, False):
        print(f"    {uc}:  {medir(uc)},")
    print("}")
