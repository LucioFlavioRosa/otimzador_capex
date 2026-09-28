"""A ETE NOVA EXPANDE POR DEMANDA, COMO A QUE JA EXISTE.

Decisao do dono do produto em 28/09/2026. A etapa inicial NAO mudou — o pacote e o
terreno mais EXATAMENTE os `modulos` do cadastro, indivisivel, porque e a ETE como
foi projetada. O que mudou e o que acontece quando a vazao conectada passa da
capacidade desse pacote:

  ANTES  `viavel()` rejeitava o PLANO INTEIRO ("vazao conectada > capacidade"). O
         otimizador entao simplesmente nao conectava nada naquele sistema, sem um
         aviso sequer. Com `modulos` em branco o teto era ZERO, e bastava uma
         sub-bacia para o sistema sair de qualquer plano: 69 ETEs assim na base de
         09/2026, cobrindo 337 sub-bacias e 126.695 ligacoes novas.

  AGORA  a vazao excedente pede modulos de expansao, como numa ETE existente. Vira
         CUSTO que o otimizador pesa, e nao um plano impossivel.

Nao havia teste nenhum cobrindo ETE nova — a suite passou inteira na mudanca.
"""
import math

import pytest

from _helpers import BANK_FIXTURE, banco, build_all, engine, silent

CAP_MOD = 150.0       # capacidade de cada modulo, na fixture
CAPEX_MOD = 500000.0
TERRENO = 300000.0
#: s1 = b1 (100) + b2 (80) = 180 de vazao; e1 e nova com 3 modulos (450 de capacidade).
VAZAO_S1 = 180.0


def _abas(**ete):
    """A fixture com a ETE `e1` LIGADA ao sistema s1, e os campos dela sobrescritos.

    A ligacao precisa ser feita aqui porque NENHUMA fixture da suite poe uma ETE na
    topologia — `ete_do_sistema` sai vazio em todas as tres, e por isso o caminho
    inteiro da ETE (dimensionamento, trava de capacidade, modulos faseados, o guarda
    de `viavel()`) nao tinha teste algum ate 28/09/2026.
    """
    d = banco(BANK_FIXTURE)
    for linha in d["ete-capex"]:
        if linha.get("ete_id") == "e1":
            linha.update(ete)
    d["sistema-topologia"].append({
        "sistema_id": "s1",
        "componente_sistema_id": "e1",
        "componente_sistema_nome": "ETE e1",
        "componente_sistema_id_jusante": None,
    })
    return d


def _ete(cen, sistema_contem="b1"):
    sis = cen.nos[sistema_contem].sistema
    return cen.ete_do_sistema[sis]


def _cen(abas, **kw):
    M = engine()
    return silent(M.ler_banco, abas, unidade="u1", **kw)


def _plano_tudo(cen):
    return {oid: max(0, int(o.inicio_min)) for oid, o in cen.obras.items() if o.eh_aegea()}


# ------------------------------------------------ a etapa inicial nao mudou
def test_demanda_dentro_do_pacote_constroi_exatamente_os_modulos_do_cadastro():
    """Mesmo sobrando capacidade: 180 de vazao cabem em 2 modulos, e ainda assim a ETE
    nasce com os 3 que a Regional cadastrou. O pacote e a ETE projetada."""
    M = engine()
    cen = _cen(_abas(modulos=3))
    silent(M.viavel, cen, _plano_tudo(cen))
    e = _ete(cen)
    assert e.n_mod == 3
    assert e.capex == pytest.approx(TERRENO + 3 * CAPEX_MOD)
    assert VAZAO_S1 < 3 * CAP_MOD, "a fixture precisa caber no pacote para este teste valer"


# ------------------------------------------------ a expansao, que e a novidade
def test_demanda_acima_do_pacote_pede_modulos_a_mais():
    """Um pacote de 1 modulo (150) para 180 de vazao: falta 30, que e 1 modulo."""
    M = engine()
    cen = _cen(_abas(modulos=1))
    silent(M.viavel, cen, _plano_tudo(cen))
    e = _ete(cen)
    esperado = 1 + math.ceil((VAZAO_S1 - CAP_MOD) / CAP_MOD)
    assert e.n_mod == esperado == 2
    assert e.capex == pytest.approx(TERRENO + 2 * CAPEX_MOD)


def test_o_plano_deixa_de_ser_rejeitado_por_capacidade():
    """O defeito que motivou a mudanca: `viavel()` devolvia False e o sistema inteiro
    saia do plano. Agora o excedente e custo, e o plano existe."""
    M = engine()
    cen = _cen(_abas(modulos=1))
    ok, _motivo = silent(M.viavel, cen, _plano_tudo(cen))
    assert ok, "vazao acima do pacote nao pode mais invalidar o plano"


def test_ete_nova_sem_modulos_nao_trava_mais_o_sistema():
    """As 69 da base real. `modulos` em branco dava capacidade zero, e QUALQUER vazao
    conectada tornava o plano inviavel — em silencio. Agora a ETE nasce so com o
    terreno e a expansao cobre a demanda inteira."""
    M = engine()
    cen = _cen(_abas(modulos=0))
    ok, _motivo = silent(M.viavel, cen, _plano_tudo(cen))
    assert ok
    e = _ete(cen)
    assert e.n_mod == math.ceil(VAZAO_S1 / CAP_MOD) == 2
    assert e.capex == pytest.approx(TERRENO + 2 * CAPEX_MOD)


def test_a_expansao_entra_no_capex_com_rotulo_proprio():
    """Quem le o plano tem de distinguir o pacote da expansao — sao decisoes
    diferentes, e so a segunda responde a demanda."""
    M = engine()
    cen = _cen(_abas(modulos=1))
    silent(M.viavel, cen, _plano_tudo(cen))
    comp = _ete(cen).capex_comp
    assert any("terreno" in k for k in comp), comp
    assert any("expansao" in k for k in comp), comp
    assert sum(comp.values()) == pytest.approx(TERRENO + 2 * CAPEX_MOD)


# ------------------------------------------------ modo faseado
def test_faseada_a_nova_ganha_obras_de_expansao_alem_do_pacote():
    cen = _cen(_abas(modulos=1), ete_faseada=True)
    mods = cen.modulos_sis[_ete(cen).sistema]
    pacote = [m for m in mods if getattr(m, "e_pacote", False)]
    expansao = [m for m in mods if getattr(m, "depende_do_pacote", False)]
    assert len(pacote) == 1, "o pacote inicial continua sendo UMA obra indivisivel"
    assert len(expansao) == 1, "e a demanda acima dele virou obra de expansao"
    assert pacote[0].cap_modulo == pytest.approx(1 * CAP_MOD)
    assert expansao[0].cap_modulo == pytest.approx(CAP_MOD)


def test_faseada_a_expansao_nunca_e_obrigatoria():
    """O pacote herda a obrigatoriedade contratual da ETE; a expansao responde a
    demanda, e obrigar a construi-la seria decidir por antecipacao o que o otimizador
    existe para decidir."""
    cen = _cen(_abas(modulos=1), ete_faseada=True)
    for m in cen.modulos_sis[_ete(cen).sistema]:
        if getattr(m, "depende_do_pacote", False):
            assert not m.obrigatoria


def test_faseada_a_trava_conta_CAPACIDADE_e_nao_cabecas():
    """O pacote de 3 modulos vale 450, nao 150. A trava somava CABECAS de modulo, o que
    so vale se todos tiverem a mesma capacidade — premissa que a ETE nova quebra."""
    cen = _cen(_abas(modulos=3), ete_faseada=True)
    mods = cen.modulos_sis[_ete(cen).sistema]
    pacote = next(m for m in mods if getattr(m, "e_pacote", False))
    assert pacote.cap_modulo == pytest.approx(3 * CAP_MOD)
    # com o pacote pronto, os 180 de vazao de s1 cabem: todas as coletas faturam
    M = engine()
    res = silent(M.avaliar, cen, _plano_tudo(cen))
    s1 = _ete(cen).sistema
    coletas = [o for o in cen.coletas if cen.nos[o.no].sistema == s1]
    assert coletas and all(res["elig"].get(o.id) for o in coletas)


# ------------------------------------------------ o relatorio do sistema
def test_o_relatorio_conta_modulos_FISICOS_e_nao_obras(tmp_path):
    """`otim_sistema.modulos_construidos` e `capacidade_instalada` alimentam a tela —
    o backend soma `modulos_construidos x capacidade_modulo` para mostrar a capacidade
    da unidade.

    O pacote da ETE nova e UMA obra que vale `modulos` modulos. Contando obras, um
    pacote de 3 apareceria como "1 modulo construido" e a capacidade sairia 3x menor.
    Antes desta mudanca isso nao aparecia porque a ETE nova tinha UMA obra so e o
    `cap_modulo` dela era o total do pacote; ao ganhar expansao, os modulos deixaram de
    ser todos iguais e a conta por cabeca passou a mentir.
    """
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.infraestrutura import persistencia as P
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen = _cen(_abas(modulos=3), ete_faseada=True)
    tabs = silent(P.materializar, cen, build_all(cen), run_id="run_ete", banco="pg")
    linha = tabs["run_sistema"].set_index("sistema").loc[_ete(cen).sistema]
    assert linha["modulos_construidos"] == 3, "o pacote de 3 vale 3 modulos, nao 1 obra"
    assert linha["capacidade_instalada"] == pytest.approx(3 * CAP_MOD)
    # e a conta que o backend faz para a tela fecha com a capacidade instalada
    assert linha["modulos_construidos"] * linha["capacidade_modulo"] == pytest.approx(
        linha["capacidade_instalada"] - linha["folga_inicial"])


# ------------------------------------------------ os outros dois caminhos da mesma regra
#
# Achados pela revisao do Codex em 28/09/2026: a primeira versao da mudanca tratou so
# `_dimensiona_etes` e `viavel()`, e deixou de fora o SOLVER e o modo `ete_fixo` — os
# dois com a regra antiga, cada um do seu jeito.
def test_o_solver_constroi_modulo_a_mais_em_vez_de_deixar_sub_bacia_de_fora():
    """O CP-SAT tinha a PROPRIA trava: `dem <= modulos x cap_modulo`. Era restricao que o
    solver nao podia comprar, entao ele simplesmente deixava sub-bacias fora do plano —
    e o plano saia "otimo" com menos receita, sem nada dizendo o porque.

    Com pacote de 1 modulo (150) e 180 de vazao em s1, as DUAS sub-bacias tem de entrar.
    """
    from _helpers import ORC_SLACK, solver_or_skip
    CP = solver_or_skip()
    M = engine()
    cen = _cen(_abas(modulos=1), orcamento=ORC_SLACK)
    res = silent(CP.resolver_cpsat, cen, max_time_s=30, workers=4)
    sis = _ete(cen).sistema
    coletas = [o for o in cen.coletas if cen.nos[o.no].sistema == sis]
    assert coletas
    construidas = [o for o in coletas if res["plano"].get(o.id) is not None]
    assert len(construidas) == len(coletas), (
        "o solver deixou sub-bacia de fora por capacidade, em vez de comprar modulo")
    silent(M.viavel, cen, res["plano"])
    assert _ete(cen).n_mod == 2


def test_modo_ete_fixo_tambem_dimensiona_a_expansao():
    """`ete_fixo` pre-dimensiona para a vazao TOTAL do sistema. Com a ETE nova presa ao
    pacote do cadastro, ele subestimava CAPEX e OPEX — 800.000 no lugar de 1.300.000 na
    fixture."""
    M = engine()
    cen = _cen(_abas(modulos=1), ete_fixo=True)
    e = _ete(cen)
    esperado = TERRENO + 2 * CAPEX_MOD
    assert e.capex_fixo == pytest.approx(esperado)
