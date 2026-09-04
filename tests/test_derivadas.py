"""Colunas DERIVADAS pela engine (nao sao input do usuario):
  - *_novas_obras = max(0, universo - atuais);
  - a engine IGNORA o valor do banco e usa o derivado (com aviso na divergencia).
"""


import pytest
from _helpers import engine, silent, BANK_CLASSE, banco


def _subop(abas):
    """A ficha de cada sub-bacia, indexada pelo id — lida da MESMA fonte que o motor.

    Ler de outro lugar tornaria o teste circular ao contrario: ele confere que o motor
    DERIVA `ligacoes_novas` de universo - atuais, e para isso precisa do numero cru que
    entrou, nao de uma segunda leitura que pode divergir."""
    return {d["sub_bacia"]: d for d in abas["subbacia-operacional"]}


def test_ligacoes_novas_e_universo_menos_atuais():
    # coleta.lig (ligacoes novas das obras) deve ser universo_ligacoes - ligacoes_atuais
    M = engine()
    abas = banco(BANK_CLASSE)
    so = _subop(abas)
    cen = silent(M.ler_banco, abas, cobertura_so_residencial=False)
    checados = 0
    for col in cen.coletas:
        d = so.get(col.no)
        if not d:
            continue
        esperado = max(0.0, float(d["universo_ligacoes"]) - float(d["ligacoes_atuais"]))
        assert col.lig == pytest.approx(esperado), f"{col.no}: {col.lig} != {esperado}"
        checados += 1
    assert checados > 0


def test_valor_do_banco_e_ignorado():
    # zera ligacoes_novas_obras no banco; a engine deve DERIVAR (universo-atuais), nao usar o 0
    M = engine()
    abas = banco(BANK_CLASSE)
    for linha in abas["subbacia-operacional"]:
        linha["ligacoes_novas_obras"] = 0
    cen = silent(M.ler_banco, abas, cobertura_so_residencial=False)
    # se a engine tivesse usado o 0 do banco, nenhuma coleta teria ligacoes novas
    assert any(col.lig > 0 for col in cen.coletas), "a engine usou o valor (errado) do banco em vez de derivar"


def test_ler_banco_nao_altera_as_abas_que_recebe():
    """O leitor NAO pode escrever na entrada — e o snapshot de auditoria depende disso.

    `ler_banco` deriva `*_novas_obras` por cima do que veio e, no modo sem CTS, troca as
    colunas exclusivas pelas consolidadas. Se essas escritas caissem no dicionario do
    chamador, o `abas_fonte` que o job usa como copia congelada do cadastro publicaria o
    dado JA DERIVADO como se fosse o input bruto: a rodada continuaria certa, e a
    auditoria passaria a mentir sobre a origem — sem erro em lugar nenhum.

    Antes o acaso protegia: o snapshot vinha de uma segunda leitura do arquivo. Com a
    fonte em memoria, os dois passaram a ser o MESMO objeto.
    """
    import copy
    M = engine()
    abas = banco(BANK_CLASSE)
    # zera para garantir que a derivacao TEM o que reescrever
    for linha in abas["subbacia-operacional"]:
        linha["ligacoes_novas_obras"] = 0
    antes = copy.deepcopy(abas)

    silent(M.ler_banco, abas, cobertura_so_residencial=False)

    assert abas == antes, "ler_banco alterou as abas recebidas"
