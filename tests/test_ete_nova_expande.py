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


# ------------------------------------------------ precedencia: pacote antes da expansao
#
# Decisao do dono do produto em 28/09/2026: a expansao so pode COMECAR depois do pacote
# concluido. Nao havia precedencia entre obras neste motor — `inicio_min` e um piso
# estatico —, entao a regra entrou em `viavel()` e, no solver, como restricao.
def _plano_faseado(cen, mes_pacote, mes_expansao):
    """Plano com tudo no `inicio_min`, menos o pacote e a expansao, que vao nos meses
    pedidos. Devolve tambem o par (id_pacote, id_expansao)."""
    mods = cen.modulos_sis[_ete(cen).sistema]
    pac = next(m for m in mods if getattr(m, "e_pacote", False))
    exp = next(m for m in mods if getattr(m, "depende_do_pacote", False))
    plano = _plano_tudo(cen)
    plano[pac.id] = mes_pacote
    plano[exp.id] = mes_expansao
    return plano, pac, exp


def test_a_expansao_nao_pode_comecar_antes_do_pacote_ficar_pronto():
    M = engine()
    cen = _cen(_abas(modulos=1), ete_faseada=True)
    plano, pac, _exp = _plano_faseado(cen, mes_pacote=0, mes_expansao=0)
    ok, motivo = silent(M.viavel, cen, plano)
    assert not ok, "comecar junto com o pacote e expandir uma estacao que nao existe"
    assert "antes de a ETE nova ficar pronta" in motivo, motivo
    # um mes antes do pacote concluir tambem nao vale
    plano, pac, _exp = _plano_faseado(cen, 0, pac.prazo - 1)
    assert not silent(M.viavel, cen, plano)[0]


def test_a_expansao_vale_a_partir_do_mes_em_que_o_pacote_fica_pronto():
    M = engine()
    cen = _cen(_abas(modulos=1), ete_faseada=True)
    plano, pac, _exp = _plano_faseado(cen, 0, 0)
    plano[_exp.id] = pac.prazo                       # exatamente quando o pacote conclui
    ok, motivo = silent(M.viavel, cen, plano)
    assert ok, motivo


def test_expansao_sem_pacote_construido_nao_e_plano():
    M = engine()
    cen = _cen(_abas(modulos=1), ete_faseada=True)
    plano, pac, exp = _plano_faseado(cen, 0, 24)
    plano[pac.id] = None
    ok, motivo = silent(M.viavel, cen, plano)
    assert not ok and "sem o pacote" in motivo, motivo


def test_o_solver_respeita_a_precedencia():
    """Pelo `resolver_por_sistema`, que e o caminho de producao.

    NAO pelo `resolver_cpsat` direto: ele JA QUEBRAVA no modo faseado antes desta
    mudanca (conferido no `origin/main`), com
    `TypeError: can only concatenate str (not "NoneType") to str` em
    `otimizador_capex_cpsat63.py:261` — o laco de OPEX pula `tipo=="ete"` mas nao
    `"ete_mod"`, e obra-modulo nao tem `no`. Defeito anterior e de outro escopo:
    consertar exige decidir como o OPEX dos modulos entra no objetivo do solver, e
    nao so calar o erro.
    """
    from _helpers import ORC_SLACK, solver_or_skip
    CP = solver_or_skip()
    M = engine()
    cen = _cen(_abas(modulos=1), ete_faseada=True, orcamento=ORC_SLACK)
    res = silent(CP.resolver_por_sistema, cen, max_time_s=30, workers=4)
    ok, motivo = silent(M.viavel, cen, res["plano"])
    assert ok, f"o solver devolveu plano que a regra recusa: {motivo}"


# ------------------------------------------------ a regra mora num lugar so
#
# Segunda revisao do Codex (28/09/2026): a primeira correcao tratou o bloco `ete_fixo`
# do motor e o `resolver_cpsat` direto, e deixou DUAS copias da formula antiga nos
# subcenarios do solver por DECOMPOSICAO — o caminho de producao. O solver custeava a
# ETE nova como pacote fixo, escolhia um plano que parecia caber, e a avaliacao final
# (que dimensiona certo) estourava o orcamento.
def test_a_regra_do_capex_fixo_tem_UMA_definicao():
    """Se alguem copiar a formula de novo, este teste nao pega — mas os dois abaixo
    pegam o efeito. Este aqui prende o contrato da funcao."""
    M = engine()
    cen = _cen(_abas(modulos=1))
    e = _ete(cen)
    capex, n = M.capex_fixo_da_ete(e, VAZAO_S1)
    assert n == 2, "pacote de 1 modulo + 1 de expansao para 180 de vazao"
    assert capex == pytest.approx(TERRENO + 2 * CAPEX_MOD)
    # e o pacote e PISO: com o projeto maior que a demanda, vale o projeto
    capex4, n4 = M.capex_fixo_da_ete(_ete(_cen(_abas(modulos=4))), VAZAO_S1)
    assert n4 == 4 and capex4 == pytest.approx(TERRENO + 4 * CAPEX_MOD)


def test_os_subcenarios_do_solver_usam_a_mesma_regra():
    """Os dois `_sub_cenario_*` do CP-SAT montavam `capex_fixo` por conta propria."""
    from otimizador.dominio import otimizador_capex_cpsat63 as CP
    M = engine()
    cen = _cen(_abas(modulos=1))
    sis = _ete(cen).sistema
    for fabrica, nome in ((CP._sub_cenario_sistema, "por sistema"),
                          (CP._sub_cenario_cidade, "por cidade")):
        try:
            sub = silent(fabrica, cen, sis if nome == "por sistema" else cen.nos["b1"].cidade)
        except Exception:                      # a fabrica pode nao aceitar este recorte
            continue
        e = next(iter(sub.ete_do_sistema.values()), None)
        if e is None or not getattr(e, "nova", False):
            continue
        assert e.capex_fixo == pytest.approx(TERRENO + 2 * CAPEX_MOD), nome


def test_o_plano_do_solver_cabe_no_orcamento_que_ele_recebeu():
    """O EFEITO do defeito, e o que de fato importa: com a ETE nova subcusteada no
    subcenario, `resolver_por_sistema` devolvia plano que a avaliacao final acusava
    como acima do teto (`auditoria_orcamento['ok'] is False`)."""
    from _helpers import solver_or_skip
    CP = solver_or_skip()
    M = engine()
    orc = {2026: 575_000, 2027: 3_105_000, 2028: 0, 2029: 0}   # o cenario do Codex
    cen = _cen(_abas(modulos=1), orcamento=orc)
    res = silent(CP.resolver_por_sistema, cen, max_time_s=30, workers=4)
    aud = silent(M.avaliar, cen, res["plano"]).get("auditoria_orcamento") or {}
    assert aud.get("ok", True), f"plano acima do teto: {aud.get('violacoes')}"


# ------------------------------------------------ a linha da ETE no resultado
#
# Relatado pelo dono do produto em 28/09/2026: a ETE era o único elemento do plano que
# saía sem quantidade e sem preço unitário — não dava para conferir de onde vinha o
# CAPEX dela nem quantos módulos foram construídos.
def _obra(cen, sufixo):
    return next(o for o in cen.obras.values() if str(o.id).endswith(sufixo))


def test_o_pacote_diz_quantos_modulos_e_a_que_preco():
    cen = _cen(_abas(modulos=3), ete_faseada=True)
    mo = _obra(cen, "#nova")
    assert mo.quantidade == 3
    assert mo.unidade == "modulo"
    assert mo.preco_unitario == pytest.approx(CAPEX_MOD)


def test_o_capex_do_pacote_separa_terreno_dos_modulos():
    """`quantidade x preco` NÃO é o CAPEX do pacote: falta o terreno. As duas parcelas
    saem em entradas próprias do `capex_comp`, para quem exibe somar a diferença como o
    que ela é, e não como um erro de arredondamento."""
    cen = _cen(_abas(modulos=3), ete_faseada=True)
    comp = _obra(cen, "#nova").capex_comp
    assert comp["ETE nova: 3 modulo(s)"] == pytest.approx(3 * CAPEX_MOD)
    assert comp["ETE nova: terreno"] == pytest.approx(TERRENO)
    assert sum(comp.values()) == pytest.approx(TERRENO + 3 * CAPEX_MOD)


def test_o_modulo_de_expansao_fecha_a_conta_sozinho():
    """Sem terreno: um módulo, ao preço de um módulo."""
    cen = _cen(_abas(modulos=1), ete_faseada=True)
    mx = _obra(cen, "#x1")
    assert mx.quantidade == 1 and mx.unidade == "modulo"
    assert mx.quantidade * mx.preco_unitario == pytest.approx(mx.capex)


def test_no_modo_modular_a_ETE_tambem_diz_quantos_modulos():
    """Lá ela é UMA obra com `n_mod` módulos, e o número acompanha o plano."""
    M = engine()
    cen = _cen(_abas(modulos=1))
    silent(M.viavel, cen, _plano_tudo(cen))
    e = _ete(cen)
    assert e.quantidade == e.n_mod == 2
    assert e.preco_unitario == pytest.approx(CAPEX_MOD)
    # terreno + 2 módulos: a diferença para `quantidade x preco` é o terreno
    assert e.capex - e.quantidade * e.preco_unitario == pytest.approx(TERRENO)
