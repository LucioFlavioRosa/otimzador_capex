"""O MÓDULO DE EXPANSÃO DA ETE NOVA TEM CAPACIDADE E PREÇO PRÓPRIOS.

Pedido do cliente, trazido pelo dono do produto em 29/09/2026: na ETE nova constrói-se uma
quantidade definida de módulos iniciais e depois expande-se se necessário — e os módulos
iniciais têm vazão e preço específicos, diferentes dos de expansão.

Quatro respostas dele que delimitam o escopo, e cada uma vira teste aqui:

1. **Vale só para a ETE NOVA.** Numa ETE existente todos os módulos continuam lendo
   `capacidade_por_modulo`/`capex_por_modulo` — são 347 ETEs no cadastro, e nenhuma pode
   mudar de número.
2. **O OPEX é o mesmo** nos dois tipos de módulo.
3. **A expansão não muda a lógica**: continua crescendo de um em um, pelo teto da vazão
   excedente.
4. **A quantidade de módulos iniciais continua em `modulos`** — a coluna não muda de
   significado nem ganha par.

Colunas vazias = o comportamento de hoje, para as 639 ETEs do cadastro não mudarem nada.

## Por que este arquivo começa por `capex_fixo_da_ete`

Porque é a peça que já falhou. Ela é a ÚNICA definição da regra de custo da ETE, usada
pelo modo `ete_fixo` e por DOIS sub-cenários do solver por decomposição — o caminho de
produção. Quando a ETE nova passou a expandir por demanda, duas cópias ficaram para trás e
o solver devolvia plano ACIMA DO ORÇAMENTO, em silêncio, achado só pela auditoria final.
Com dois preços, o mesmo esquecimento reaparece igual.
"""
import pytest

from _helpers import engine, silent
from test_ete_nova_expande import (CAPEX_MOD, CAP_MOD, TERRENO, VAZAO_S1, _abas, _cen,
                                   _ete, _plano_tudo as _plano)

#: O módulo de expansão da fixture: MENOR e MAIS BARATO que o inicial, e os dois sentidos
#: de propósito. Se a conta trocasse os dois, um módulo "igual mas diferente" poderia
#: passar despercebido; assim qualquer troca muda capacidade E custo, em direções opostas.
CAP_EXP = 60.0
CAPEX_EXP = 260000.0


def _cen_exp(modulos=1, **kw):
    return _cen(_abas(modulos=modulos,
                      capacidade_por_modulo_expansao=CAP_EXP,
                      capex_por_modulo_expansao=CAPEX_EXP), **kw)


def _obra(cen, sufixo):
    return next(o for o in cen.obras.values() if str(o.id).endswith(sufixo))


# ------------------------------------------------ a regra do custo, onde ela mora
def test_o_pacote_usa_o_MODULO_INICIAL_e_a_expansao_o_DELA():
    """A conta de `capex_fixo_da_ete`, com os dois preços.

    Vazão 180, pacote de 1 módulo de 150 → sobram 30, que pedem 1 módulo de expansão de
    60. O custo é `terreno + 1×500.000 + 1×260.000`, e não `terreno + 2×500.000`.
    """
    capex, n = engine().capex_fixo_da_ete(_ete(_cen_exp(modulos=1)), VAZAO_S1)
    assert n == 2, "1 módulo de pacote + 1 de expansão"
    assert capex == pytest.approx(TERRENO + CAPEX_MOD + CAPEX_EXP)


def test_a_QUANTIDADE_de_expansao_sai_da_capacidade_DELA():
    """Módulo de expansão menor ⇒ precisam-se de mais.

    Com 180 de vazão o excedente cabe em um módulo dos dois tamanhos, e o teste passaria
    sem provar nada. Com 400, o excedente de 250 pede CINCO módulos de 60 e caberia em
    DOIS de 150 — aí os dois divisores dão números diferentes.
    """
    _, n = engine().capex_fixo_da_ete(_ete(_cen_exp(modulos=1)), 400.0)
    assert n == 1 + 5, "250 de excedente / 60 por módulo de expansão = 5"


def test_o_pacote_continua_sendo_PISO():
    """Projeto maior que a demanda: vale o projeto, e sem nenhuma expansão."""
    capex, n = engine().capex_fixo_da_ete(_ete(_cen_exp(modulos=4)), VAZAO_S1)
    assert n == 4
    assert capex == pytest.approx(TERRENO + 4 * CAPEX_MOD)


def test_SEM_AS_COLUNAS_NOVAS_nada_muda():
    """O default: coluna vazia = o módulo de expansão é igual ao inicial.

    É o que preserva as 639 ETEs do cadastro. Sem esta garantia, a migração mudaria o
    número de todas elas no dia em que subisse.
    """
    capex, n = engine().capex_fixo_da_ete(_ete(_cen(_abas(modulos=1))), VAZAO_S1)
    assert n == 2
    assert capex == pytest.approx(TERRENO + 2 * CAPEX_MOD)


def test_UM_ZERO_DECLARADO_nao_e_coluna_vazia():
    """Preço zero é um preço, e não uma ausência.

    A leitura guarda `None` em vez de `0.0` justamente para separar os dois casos: um
    módulo de expansão sem custo é decisão do cadastro, e cair no preço do módulo inicial
    seria cobrar 500.000 por algo que a Regional disse que não custa.
    """
    capex, n = engine().capex_fixo_da_ete(
        _ete(_cen(_abas(modulos=1, capacidade_por_modulo_expansao=CAP_EXP,
                        capex_por_modulo_expansao=0))), VAZAO_S1)
    assert n == 2
    assert capex == pytest.approx(TERRENO + CAPEX_MOD), "o módulo de expansão é de graça"


def test_A_ETE_EXISTENTE_NAO_E_AFETADA():
    """Resposta 1 do cliente: a distinção vale só para a ETE nova.

    Numa ETE que já existe todos os módulos são expansão no sentido físico — mas eles
    continuam lendo `capex_por_modulo`, porque ali essa coluna já significa "o módulo que
    eu construo". Preencher as colunas novas não pode mexer nelas.

    `capex_terreno=0` é obrigatório na fixture: o motor DEDUZ `nova` de terreno maior que
    zero (`eo.nova = flag or capex_terreno > 1e-9`), então uma ETE com terreno é nova ainda
    que o cadastro diga "Não". No cadastro real isso não separa ninguém — as 292 novas são
    novas por flag —, mas no teste separa.
    """
    abas = _abas(modulos=0, nova="Nao", capex_terreno=0.0, capacidade_ociosa=0.0,
                 capacidade_por_modulo_expansao=CAP_EXP,
                 capex_por_modulo_expansao=CAPEX_EXP)
    capex, n = engine().capex_fixo_da_ete(_ete(_cen(abas)), VAZAO_S1)
    assert n == 2, "180 de vazão / 150 por módulo = 2"
    assert capex == pytest.approx(2 * CAPEX_MOD), "usou o preço de expansão numa ETE existente"


def test_A_QUANTIDADE_INICIAL_CONTINUA_EM_modulos():
    """Resposta 4 dele: a coluna não muda de significado nem ganha par.

    O pacote é exatamente `modulos` módulos iniciais, e o que passa disso é expansão.
    """
    cen = _cen_exp(modulos=3)
    e = _ete(cen)
    assert e.modulos == 3
    _, n = engine().capex_fixo_da_ete(e, VAZAO_S1)
    assert n == 3, "3 × 150 = 450 de capacidade cobre os 180 de vazão, sem expansão"


# ------------------------------------------------ o modo faseado, que é o de produção
#
# `job_databricks` liga `ete_faseada` em toda rodada, então é aqui que a regra de fato
# roda. Cada módulo é uma obra, e cada obra carrega a capacidade e o preço DELA.
def test_faseada_o_PACOTE_continua_com_o_modulo_inicial():
    cen = _cen_exp(modulos=2, ete_faseada=True)
    mo = _obra(cen, "#nova")
    assert mo.quantidade == 2 and mo.preco_unitario == pytest.approx(CAPEX_MOD)
    assert mo.cap_modulo == pytest.approx(2 * CAP_MOD), "o pacote vale a capacidade dos 2"
    assert mo.capex == pytest.approx(TERRENO + 2 * CAPEX_MOD)


def test_faseada_a_OBRA_de_expansao_tem_a_capacidade_e_o_preco_dela():
    """Vazão 180, pacote de 1 módulo de 150: sobram 30, que cabem num módulo de 60."""
    cen = _cen_exp(modulos=1, ete_faseada=True)
    mx = _obra(cen, "#x1")
    assert mx.cap_modulo == pytest.approx(CAP_EXP)
    assert mx.preco_unitario == pytest.approx(CAPEX_EXP)
    assert mx.quantidade * mx.preco_unitario == pytest.approx(mx.capex), "a linha fecha"


def test_faseada_a_QUANTIDADE_de_obras_de_expansao_sai_da_capacidade_dela():
    """Sem pacote nenhum (`modulos=0`): os 180 de vazão pedem TRÊS módulos de 60, e não
    os dois que os de 150 resolveriam. É o caso das 69 ETEs novas sem `modulos`."""
    cen = _cen_exp(modulos=0, ete_faseada=True)
    exp = [o for o in cen.obras.values() if getattr(o, "depende_do_pacote", False)]
    assert len(exp) == 3
    assert all(o.cap_modulo == pytest.approx(CAP_EXP) for o in exp)
    assert all(o.preco_unitario == pytest.approx(CAPEX_EXP) for o in exp)


def test_faseada_o_OPEX_do_modulo_de_expansao_e_o_MESMO():
    """Resposta 2 do cliente: o OPEX é por módulo e não distingue o tipo. Com o pacote de
    um módulo, os dois têm de sair iguais."""
    cen = _cen_exp(modulos=1, ete_faseada=True)
    assert _obra(cen, "#x1").opex_ano == pytest.approx(_obra(cen, "#nova").opex_ano)
    assert _obra(cen, "#x1").opex_ano > 0, "OPEX zero faria o teste passar sem dizer nada"


def test_faseada_a_trava_SOMA_capacidade_de_modulos_de_tamanhos_DIFERENTES():
    """Com pacote de 150 e expansão de 60, a capacidade construída é 210 para 180 de
    vazão — e as duas sub-bacias faturam. Contando cabeças (2 × 150 = 300 ou 2 × 60 = 120)
    o resultado seria generoso demais ou apertado demais."""
    M = engine()
    cen = _cen_exp(modulos=1, ete_faseada=True)
    plano = _plano(cen)
    plano[_obra(cen, "#x1").id] = _obra(cen, "#nova").prazo   # a expansão só após o pacote
    res = silent(M.avaliar, cen, plano)
    sis = _ete(cen).sistema
    coletas = [o for o in cen.coletas if cen.nos[o.no].sistema == sis]
    assert coletas and all(res["elig"].get(o.id) for o in coletas), \
        "a soma das capacidades (150+60=210) cobre os 180 de vazão"


def test_faseada_SEM_o_modulo_de_expansao_a_vazao_NAO_cabe():
    """A contraprova do teste acima: com o pacote sozinho (150) os 180 não caberiam. Sem
    isto, o anterior passaria mesmo se a trava ignorasse capacidade."""
    M = engine()
    cen = _cen_exp(modulos=1, ete_faseada=True)
    plano = _plano(cen)
    plano[_obra(cen, "#x1").id] = None
    res = silent(M.avaliar, cen, plano)
    sis = _ete(cen).sistema
    coletas = [o for o in cen.coletas if cen.nos[o.no].sistema == sis]
    assert not all(res["elig"].get(o.id) for o in coletas)


# ------------------------------------------------ o modo modular: o unitário da linha
def test_no_modo_modular_o_UNITARIO_deixa_de_existir_quando_ha_dois_precos():
    """Decisão do dono do produto em 29/09/2026: as parcelas são publicadas separadas, e
    onde um unitário não fecharia a conta ele sai VAZIO em vez de sair errado.

    `quantidade × unitário` é o que a tela soma; com 1 módulo a 500.000 e 1 a 260.000,
    qualquer um dos dois preços daria um total que não existe.
    """
    M = engine()
    cen = _cen_exp(modulos=1)
    silent(M.viavel, cen, _plano(cen))
    e = _ete(cen)
    assert e.n_mod == 2 and e.quantidade == 2
    assert e.preco_unitario is None, "dois preços na mesma ETE não têm unitário"
    assert e.capex == pytest.approx(TERRENO + CAPEX_MOD + CAPEX_EXP)


def test_no_modo_modular_o_UNITARIO_CONTINUA_quando_ha_um_preco_so():
    """O cadastro de hoje inteiro. Sem esta garantia a mudança apagaria o unitário das
    639 ETEs publicadas."""
    M = engine()
    cen = _cen(_abas(modulos=1))
    silent(M.viavel, cen, _plano(cen))
    assert _ete(cen).preco_unitario == pytest.approx(CAPEX_MOD)


def test_no_modo_modular_o_unitario_SOBREVIVE_a_precos_IGUAIS_declarados():
    """Coluna preenchida com o MESMO preço do módulo inicial: é um preço só, e o unitário
    continua existindo. O critério é o preço, e não a presença da coluna."""
    M = engine()
    cen = _cen(_abas(modulos=1, capacidade_por_modulo_expansao=CAP_EXP,
                     capex_por_modulo_expansao=CAPEX_MOD))
    silent(M.viavel, cen, _plano(cen))
    assert _ete(cen).preco_unitario == pytest.approx(CAPEX_MOD)


def test_as_PARCELAS_do_capex_separam_os_dois_precos():
    """O que a tela soma: terreno com módulos iniciais numa parcela, expansão na outra."""
    M = engine()
    cen = _cen_exp(modulos=1)
    silent(M.viavel, cen, _plano(cen))
    comp = _ete(cen).capex_comp
    assert comp["ETE nova: terreno + 1 mod"] == pytest.approx(TERRENO + CAPEX_MOD)
    assert comp["ETE nova: expansao 1 mod"] == pytest.approx(CAPEX_EXP)
    assert sum(comp.values()) == pytest.approx(_ete(cen).capex)


def test_a_contagem_por_PRECO_acompanha_o_plano():
    """`n_mod_ini` e `n_mod_exp` são o que a publicação vai usar para as parcelas.

    Eles separam os módulos pelo PREÇO pago, e não pela fase em que entraram: numa ETE
    existente todo módulo custa `capex_por_modulo`, e portanto todos são `ini`.
    """
    M = engine()
    cen = _cen_exp(modulos=1)
    silent(M.viavel, cen, _plano(cen))
    e = _ete(cen)
    assert (e.n_mod_ini, e.n_mod_exp) == (1, 1)
    assert e.n_mod_ini + e.n_mod_exp == e.n_mod
    cen2 = _cen(_abas(modulos=0, nova="Nao", capex_terreno=0.0, capacidade_ociosa=0.0,
                      capacidade_por_modulo_expansao=CAP_EXP,
                      capex_por_modulo_expansao=CAPEX_EXP))
    silent(M.viavel, cen2, _plano(cen2))
    ex = _ete(cen2)
    assert (ex.n_mod_ini, ex.n_mod_exp) == (ex.n_mod, 0) and ex.n_mod == 2


# ------------------------------------------------ o caminho do solver de produção
#
# `resolver_por_sistema` monta as colunas candidatas em `_montar_faseado`, e
# `_colunas_faseada` DESCARTA EM SILÊNCIO toda coluna que `viavel()` recusa
# (`if not M.viavel(sub,pl)[0]: continue`). Uma coluna mal montada não dá erro: ela
# simplesmente deixa de existir, e o sistema entra no mestre valendo menos — ou nada.
def _sub_e_plano(cen, CP):
    sis = _ete(cen).sistema
    sub = silent(CP._sub_cenario_sistema, cen, sis)
    built = {sb for sb in sub.nos if sub.nos[sb].sistema == sis}
    return sub, sis, CP._montar_faseado(sub, built)


def test_montar_faseado_LIGA_MODULOS_ATE_A_CAPACIDADE_COBRIR_A_VAZAO():
    """A conta era `teto(vazão / cap_modulo)` comparada com o número de OBRAS.

    Pacote de 1 módulo de 150 e expansão de 10 em 10, para 180 de vazão: contando cabeças
    dá 2 obras — pacote + uma expansão, 160 de capacidade, que não cobre os 180. Somando
    capacidade, ligam-se os quatro módulos e a conta fecha exatamente.
    """
    from _helpers import solver_or_skip
    CP = solver_or_skip()
    cen = _cen(_abas(modulos=1, capacidade_por_modulo_expansao=10.0,
                     capex_por_modulo_expansao=20000.0), ete_faseada=True)
    sub, sis, pl = _sub_e_plano(cen, CP)
    mods = sub.modulos_sis[sis]
    ligados = [m for m in mods if pl.get(m.id) is not None]
    assert len(ligados) == 4, [m.id for m in ligados]
    assert sum(m.cap_modulo for m in ligados) == pytest.approx(VAZAO_S1)


def test_montar_faseado_RESPEITA_a_precedencia_do_pacote():
    """A expansão só começa com o pacote pronto — a mesma regra que `viavel()` cobra, e
    que esta função ignorava (defeito de 28/09/2026, achado em 29/09).

    Como ela agendava tudo no `inicio_min`, a expansão começava junto com o pacote, a
    coluna era recusada, e de uma ETE nova que precisasse expandir sobrava só a coluna
    "não constrói nada". No cenário desta fixture isso levava o sistema inteiro a ficar
    fora do plano.
    """
    from _helpers import solver_or_skip
    CP = solver_or_skip()
    M = engine()
    cen = _cen_exp(modulos=1, ete_faseada=True)
    sub, sis, pl = _sub_e_plano(cen, CP)
    pac = next(m for m in sub.modulos_sis[sis] if getattr(m, "e_pacote", False))
    exp = [m for m in sub.modulos_sis[sis] if getattr(m, "depende_do_pacote", False)]
    assert exp, "a fixture precisa ter expansão para este teste dizer algo"
    for m in exp:
        assert pl[m.id] >= pl[pac.id] + pac.prazo, f"{m.id} começa antes de a ETE existir"
    ok, motivo = silent(M.viavel, sub, pl)
    assert ok, f"a coluna candidata seria descartada em silêncio: {motivo}"


def test_a_ETE_NOVA_com_expansao_NAO_perde_as_colunas_candidatas():
    """O efeito do defeito acima, no solver de produção — e sem nenhuma coluna nova
    preenchida, porque ele não tem nada a ver com a mudança de 29/09/2026.

    Com `modulos=0` (as 69 ETEs novas sem módulos no cadastro de 09/2026) toda a
    capacidade vem de expansão. O sistema tem de entrar no plano.
    """
    from _helpers import ORC_SLACK, solver_or_skip
    CP = solver_or_skip()
    M = engine()
    cen = _cen(_abas(modulos=0), ete_faseada=True, orcamento=ORC_SLACK)
    res = silent(CP.resolver_por_sistema, cen, max_time_s=30, workers=4)
    ok, motivo = silent(M.viavel, cen, res["plano"])
    assert ok, motivo
    sis = _ete(cen).sistema
    coletas = [o for o in cen.coletas if cen.nos[o.no].sistema == sis]
    feitas = [o for o in coletas if res["plano"].get(o.id) is not None]
    assert len(feitas) == len(coletas) == 2, "o sistema ficou fora do plano por inteiro"


def test_o_solver_de_producao_CONSTROI_com_os_dois_precos():
    """O mesmo, com o módulo de expansão próprio: 4 módulos de 60 em vez de 3 de 150."""
    from _helpers import ORC_SLACK, solver_or_skip
    CP = solver_or_skip()
    M = engine()
    cen = _cen_exp(modulos=0, ete_faseada=True, orcamento=ORC_SLACK)
    res = silent(CP.resolver_por_sistema, cen, max_time_s=30, workers=4)
    ok, motivo = silent(M.viavel, cen, res["plano"])
    assert ok, motivo
    sis = _ete(cen).sistema
    coletas = [o for o in cen.coletas if cen.nos[o.no].sistema == sis]
    assert len([o for o in coletas if res["plano"].get(o.id) is not None]) == len(coletas)
    mods = [m for m in cen.modulos_sis[sis] if res["plano"].get(m.id) is not None]
    assert sum(m.capex for m in mods) == pytest.approx(TERRENO + 3 * CAPEX_EXP)


def test_o_plano_do_solver_cabe_no_orcamento_COM_DOIS_PRECOS():
    """O subcenário custeia a ETE por `capex_fixo_da_ete`; se ele ignorasse o preço da
    expansão, o mestre escolheria um plano que parece caber e a auditoria final acusaria o
    estouro. É o defeito que já aconteceu uma vez, com um preço só."""
    from _helpers import solver_or_skip
    CP = solver_or_skip()
    M = engine()
    orc = {2026: 575_000, 2027: 3_105_000, 2028: 0, 2029: 0}
    cen = _cen_exp(modulos=1, orcamento=orc)
    res = silent(CP.resolver_por_sistema, cen, max_time_s=30, workers=4)
    aud = silent(M.avaliar, cen, res["plano"]).get("auditoria_orcamento") or {}
    assert aud.get("ok", True), f"plano acima do teto: {aud.get('violacoes')}"

# ------------------------------------------------ o que a REVISÃO do Codex achou
#
# Dois consumidores que ficaram com a premissa de módulos iguais. Nenhum dos dois era
# alcançado pelos testes acima, e é por isso que eles estão aqui, nomeados pelo efeito.
def test_a_CAPACIDADE_PUBLICADA_soma_a_de_cada_modulo():
    """`otim_sistema.capacidade_instalada` é o que a tela mostra como capacidade da ETE,
    e dela saem `ocupacao_pct` e `folga_remanescente`.

    A conta era `folga + modulos_construidos × capacidade_por_modulo` — o número de
    módulos FÍSICOS vezes a capacidade do módulo INICIAL. Com pacote de 150 e expansão de
    60, ela publicava 300 de capacidade onde há 210: ocupação 60% no lugar de 85,7%, e
    folga de 120 onde sobram 30. Um gargalo de tratamento aparecendo como sobra.
    """
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.infraestrutura import persistencia as P
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen = _cen_exp(modulos=1, ete_faseada=True)
    plano = _plano(cen)
    plano[_obra(cen, "#x1").id] = _obra(cen, "#nova").prazo
    res = silent(M.avaliar, cen, plano)
    tabs = silent(P.materializar, cen, res, run_id="run_exp", banco="pg")
    linha = tabs["run_sistema"].set_index("sistema").loc[_ete(cen).sistema]
    assert linha["modulos_construidos"] == 2, "um módulo inicial e um de expansão"
    assert linha["capacidade_instalada"] == pytest.approx(CAP_MOD + CAP_EXP)
    assert linha["ocupacao_pct"] == pytest.approx(VAZAO_S1 / (CAP_MOD + CAP_EXP) * 100.0)
    assert linha["folga_remanescente"] == pytest.approx(CAP_MOD + CAP_EXP - VAZAO_S1)


def test_a_capacidade_publicada_NAO_muda_com_modulos_iguais():
    """O guarda do teste acima: onde os módulos são iguais, a soma das capacidades dá
    exatamente o que a multiplicação dava. Nenhuma rodada publicada muda de número."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.infraestrutura import persistencia as P
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen = _cen(_abas(modulos=2), ete_faseada=True)
    tabs = silent(P.materializar, cen, silent(M.avaliar, cen, _plano(cen)),
                  run_id="run_igual", banco="pg")
    linha = tabs["run_sistema"].set_index("sistema").loc[_ete(cen).sistema]
    assert linha["capacidade_instalada"] == pytest.approx(
        linha["modulos_construidos"] * linha["capacidade_modulo"] + linha["folga_inicial"])


def test_o_CPSAT_DIRETO_nao_devolve_plano_acima_do_orcamento():
    """O cenário do Codex, com o módulo de expansão MENOR e ao MESMO preço do inicial.

    O modelo do CP-SAT direto tinha uma capacidade e um preço só (`nmod × cap_modulo >=
    demanda`, custo `nmod × capex_modulo`). Com expansão de 10 para 150 de módulo inicial,
    ele achava que a ETE custava 2 módulos onde a regra real cobra 4 — e devolvia OTIMO
    com um plano que `viavel()` recusa por orçamento.
    """
    from _helpers import solver_or_skip
    CP = solver_or_skip()
    M = engine()
    orc = {2026: 4_000_000, 2027: 4_000_000, 2028: 0, 2029: 0}
    cen = _cen(_abas(modulos=1, capacidade_por_modulo_expansao=10.0,
                     capex_por_modulo_expansao=CAPEX_MOD), orcamento=orc)
    res = silent(CP.resolver_cpsat, cen, max_time_s=30, workers=4)
    ok, motivo = silent(M.viavel, cen, res["plano"])
    assert ok, f"o solver devolveu plano que a regra recusa: {motivo}"


def test_o_CPSAT_DIRETO_compra_a_QUANTIDADE_certa_de_expansao():
    """E a capacidade que ele modela é a de cada tipo: 180 de vazão com pacote de 150 e
    expansão de 10 pede TRÊS módulos de expansão, não um."""
    from _helpers import ORC_SLACK, solver_or_skip
    CP = solver_or_skip()
    M = engine()
    cen = _cen(_abas(modulos=1, capacidade_por_modulo_expansao=10.0,
                     capex_por_modulo_expansao=20000.0), orcamento=ORC_SLACK)
    res = silent(CP.resolver_cpsat, cen, max_time_s=30, workers=4)
    sis = _ete(cen).sistema
    coletas = [o for o in cen.coletas if cen.nos[o.no].sistema == sis]
    feitas = [o for o in coletas if res["plano"].get(o.id) is not None]
    assert len(feitas) == len(coletas), "deixou sub-bacia de fora em vez de comprar módulo"
    silent(M.viavel, cen, res["plano"])
    assert _ete(cen).n_mod == 1 + 3, f"n_mod={_ete(cen).n_mod}"
