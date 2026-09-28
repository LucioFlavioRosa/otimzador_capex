"""O TICKET E A RECEITA DIVIDIDA PELAS LIGACOES TOTAIS — nao pelas atuais.

Defeito relatado pelo dono do produto em 28/09/2026: o ticket vinha de
`receita / ligacoes_atuais`. As duas pontas da divisao tem escopos diferentes —
a receita e a de AGUA da sub-bacia inteira (todas as ligacoes que faturam agua),
e `ligacoes_atuais` e a base JA ATENDIDA com esgoto, o numerador da cobertura.
Dividir uma pela outra inflava o ticket por 1/cobertura.

O denominador correto e `universo_ligacoes` — o `QTD_LIGACOES_TOTAL` da origem.
E o ticket e o da AGUA: a tarifa de esgoto e ele vezes o fator de paridade.

Os tres pontos que estes testes travam:
  * o ticket da OBRA (`ticket_mes`), que multiplica as ligacoes novas -> receita;
  * o ticket do RELATORIO (`sub_receita[...]["ticket"]`), base do efeito-base;
  * que `atuais` continua sendo `ligacoes_atuais` — quem paga esgoto hoje nao
    mudou, so o preco por ligacao mudou de denominador.

A base de receita da rodada (faturada|arrecadada) continua escolhendo o
NUMERADOR: a correcao e do denominador, e uma nao apaga a outra.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _helpers import BANK_CLASSE, BANK_CTS, banco, engine, silent  # noqa: E402

#: b1 na fixture: universo 1.000, atuais 400, faturada 200.000, arrecadada 180.000.
B1_UNIVERSO, B1_ATUAIS = 1000, 400
B1_FATURADA, B1_ARRECADADA = 200000, 180000


def _cen(base_receita="arrecadada"):
    M = engine()
    return silent(M.ler_banco, banco(BANK_CTS), usar_cts=False, base_receita=base_receita)


def _obra_de_coleta(cen, sub_bacia):
    """A obra de LIGACAO da sub-bacia — a unica que carrega ticket e ligacoes."""
    return next(o for o in cen.coletas if o.no == sub_bacia)


@pytest.mark.parametrize("base,receita", [("arrecadada", B1_ARRECADADA),
                                          ("faturada", B1_FATURADA)])
def test_ticket_da_obra_divide_pelas_ligacoes_totais(base, receita):
    cen = _cen(base)
    assert _obra_de_coleta(cen, "b1").ticket_mes == pytest.approx(receita / B1_UNIVERSO)


@pytest.mark.parametrize("base,receita", [("arrecadada", B1_ARRECADADA),
                                          ("faturada", B1_FATURADA)])
def test_ticket_do_relatorio_divide_pelas_ligacoes_totais(base, receita):
    sr = _cen(base).sub_receita["b1"]
    assert sr["ticket"] == pytest.approx(receita / B1_UNIVERSO)
    assert sr["base_receita"] == base


def test_atuais_continua_sendo_a_base_ja_atendida():
    """O efeito-base multiplica `atuais x ticket`: com o denominador certo isso da a
    receita de agua de quem TEM esgoto, e nao a da sub-bacia inteira."""
    sr = _cen().sub_receita["b1"]
    assert sr["atuais"] == pytest.approx(B1_ATUAIS)
    esperado = B1_ARRECADADA * (B1_ATUAIS / B1_UNIVERSO)
    assert sr["atuais"] * sr["ticket"] == pytest.approx(esperado)


def test_o_denominador_antigo_ficaria_inflado_por_um_sobre_a_cobertura():
    """A conta do defeito, escrita como numero: 450 contra 180 em b1."""
    cen = _cen()
    ticket = _obra_de_coleta(cen, "b1").ticket_mes
    inflacao = B1_UNIVERSO / B1_ATUAIS
    assert B1_ARRECADADA / B1_ATUAIS == pytest.approx(ticket * inflacao)


def test_universo_zerado_nao_divide_por_zero():
    """`_com_cts` vazia com coletor por perto zera universo e receita juntos: o ticket
    sai 0,0, e nao um ZeroDivisionError nem um infinito."""
    M = engine()
    abas = banco(BANK_CTS)
    for linha in abas["subbacia-operacional"]:
        if linha.get("sub_bacia") == "b1":
            linha["universo_ligacoes"] = 0
    cen = silent(M.ler_banco, abas, usar_cts=False)
    assert cen.sub_receita["b1"]["ticket"] == 0.0


# --------------------------------------------- o universo segue a regua da rodada
#
# Decisao do dono do produto em 28/09/2026, depois da correcao do denominador: o
# universo do ticket tem de ser o MESMO universo que a rodada esta medindo. Com o
# recorte residencial ligado, e o residencial; sem ele, o total.
def _obra(cen, sub_bacia):
    return next(o for o in cen.coletas if o.no == sub_bacia)


@pytest.mark.parametrize("so_residencial,coluna", [(False, "universo_ligacoes"),
                                                   (True, "universo_ligacoes_residencial")])
def test_o_universo_do_ticket_e_o_que_a_rodada_mede(so_residencial, coluna):
    M = engine()
    abas = banco(BANK_CLASSE)
    linha = next(r for r in abas["subbacia-operacional"] if r.get("sub_bacia") == "b1")
    esperado = linha["receita_arrecadada_media_mensal"] / linha[coluna]
    cen = silent(M.ler_banco, abas, unidade="u1", cobertura_so_residencial=so_residencial)
    assert _obra(cen, "b1").ticket_mes == pytest.approx(esperado)
    assert cen.sub_receita["b1"]["ticket"] == pytest.approx(esperado)


def test_sem_a_coluna_residencial_a_sub_bacia_cai_para_o_universo_total():
    """Mesma degradacao por sub-bacia que a cobertura ja faz: sem a coluna, aquela
    linha mede no total — e o motor avisa, em vez de inventar o dado."""
    M = engine()
    abas = banco(BANK_CLASSE)
    linha = next(r for r in abas["subbacia-operacional"] if r.get("sub_bacia") == "b1")
    linha["universo_ligacoes_residencial"] = None
    cen = silent(M.ler_banco, abas, unidade="u1", cobertura_so_residencial=True)
    esperado = linha["receita_arrecadada_media_mensal"] / linha["universo_ligacoes"]
    assert _obra(cen, "b1").ticket_mes == pytest.approx(esperado)
