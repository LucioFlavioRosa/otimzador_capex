"""Recalcula os numeros golden do banco de teste CTS e imprime o bloco GOLDEN pronto para colar
em test_regressao_golden.py. Use APENAS quando a mudanca de resultado for intencional:

    python tests/atualiza_golden.py

Revise o diff antes de colar — e justamente essa revisao que garante que nenhuma regressao passe
despercebida."""
from _helpers import load_cts, build_all, capex_total, cobertura_fim


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
