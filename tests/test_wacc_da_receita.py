# -*- coding: utf-8 -*-
"""O WACC que desconta a receita olha SO as obras da propria sub-bacia.

Ate 10/09/2026 ele era a media da CADEIA INTEIRA ate a ETE — a ligacao e a rede dela,
o transporte de todos os nos a jusante e a ETE do sistema —, ponderada pelo CAPEX
rateado por vazao. O custo de capital de uma sub-bacia dependia de QUEM ESTAVA A
JUSANTE dela.

POR QUE ESTE ARQUIVO EXISTE: o golden de regressao (`test_regressao_golden.py`) e
CEGO a esta formula. As 30 obras de `banco_teste_CTS_poc_v2.json` tem todas o mesmo
WACC (0,09), e media ponderada de valores iguais da o mesmo numero nao importa quais
entrem na conta. Ele passou sem alteracao quando a formula mudou — e passaria de novo
se ela voltasse. Aqui os WACCs sao DIFERENTES de proposito, que e a unica forma de a
conta ter como errar.
"""

import pytest
from _helpers import engine


def cenario():
    """A --> B --> ETE, com o tronco caro ancorado no no de B.

    A depende do tronco de B para escoar, mas ele nao e obra DELA. A ETE tambem nao e
    de ninguem em particular. Os tres WACCs sao distintos para que cada exclusao mude
    o resultado de forma visivel.
    """
    M = engine()
    nos = [M.No("A", "cidade1", "sis1", "reg1", "B"),
           M.No("B", "cidade1", "sis1", "reg1", "ETE")]
    cidades = [M.Cidade("cidade1", 0.3, 1000, 0.1)]
    obras = [
        M.Obra("lig_A", "coleta", no="A", capex_comp={"lig": 100.0}, wacc=0.10),
        M.Obra("rede_A", "rede", no="A", capex_comp={"rede": 200.0}, wacc=0.10),
        M.Obra("lig_B", "coleta", no="B", capex_comp={"lig": 100.0}, wacc=0.10),
        M.Obra("rede_B", "rede", no="B", capex_comp={"rede": 200.0}, wacc=0.10),
        M.Obra("tro_B", "transporte", no="B", capex_comp={"tro": 1000.0}, wacc=0.20),
        M.Obra("ete_sis1", "ete", sistema="sis1", capex_comp={"ete": 5000.0}, wacc=0.05),
    ]
    cen = M.Cenario(nos, cidades, obras, {"reg1": 10 ** 9}, anos=20)
    cen.vazao = {"A": 50.0, "B": 50.0}
    return M, cen


def taxa(M, cen, no):
    return M._wacc_receita(cen, next(o for o in cen.coletas if o.no == no))


def test_o_tronco_a_jusante_nao_entra_no_wacc_da_sub_bacia():
    """A so tem ligacao e rede, as duas a 10% -> 10%, e nao a media com o tronco de B."""
    M, cen = cenario()
    assert taxa(M, cen, "A") == pytest.approx(0.10)


def test_o_tronco_ancorado_na_propria_sub_bacia_entra():
    """O tronco esta no no de B: e obra dela, e pesa pelo CAPEX.

    (100*0,10 + 200*0,10 + 1000*0,20) / 1300 = 0,176923...
    """
    M, cen = cenario()
    assert taxa(M, cen, "B") == pytest.approx((100 * .10 + 200 * .10 + 1000 * .20) / 1300)


def test_a_ete_nao_pesa_em_ninguem():
    """A ETE custa 5000 a 5% — de longe o maior CAPEX do sistema.

    Se ela entrasse, as duas taxas desabariam na direcao de 5%. Nenhuma das duas fica
    abaixo do menor WACC das obras proprias.
    """
    M, cen = cenario()
    assert taxa(M, cen, "A") >= 0.10
    assert taxa(M, cen, "B") >= 0.10


def test_duas_sub_bacias_iguais_recebem_a_mesma_taxa():
    """A invariante que a formula antiga quebrava.

    A e B tem exatamente as mesmas obras proprias (100 @10% + 200 @10%); a unica
    diferenca entre elas e a POSICAO no fluxo. Antes, a posicao mudava a taxa — as duas
    davam 16,25% por causa do tronco e da ETE. Tirando o tronco de B, a posicao deixa de
    contar.
    """
    M, cen = cenario()
    cen.obras.pop("tro_B")
    assert taxa(M, cen, "A") == pytest.approx(taxa(M, cen, "B"))


def test_sub_bacia_sem_capex_proprio_cai_no_wacc_da_ligacao():
    """Terceiro (CAPEX 0) nao pesa; sem CAPEX proprio, sobra a taxa da propria ligacao."""
    M, cen = cenario()
    cen.obras["lig_A"].capex = 0.0
    cen.obras["rede_A"].capex = 0.0
    assert taxa(M, cen, "A") == pytest.approx(cen.obras["lig_A"].wacc)
