"""CTS (Coletor de Tempo Seco) — as duas visoes (usar_cts ligado x desligado) sao a MESMA
demanda. O que TEM de bater: cobertura, vazao, universo efetivo. O que TEM de diferir de
proposito: numero de obras, CAPEX e VPL. Estes testes travam esse contrato."""
import pytest
from _helpers import engine, load_fixture, capex_total, cobertura_fim, codigo

CTS_COMPONENTES = {"cts", "tro", "eee", "lr"}   # Coletor de tempo seco + Tronco + EEE + Linha de recalque


# ---------------------------------------------------------------- estrutura
def test_cts_entram_como_nos_no_modo_ligado(cen_on, cen_off):
    assert cen_on.cts_ids, "modo ligado deveria ter CTS carregadas"
    assert not cen_off.cts_ids, "modo desligado nao deve ter nenhuma CTS"
    assert all(cen_on.nos[c].is_cts for c in cen_on.cts_ids)
    assert not any(getattr(n, "is_cts", False) for n in cen_off.nos.values())


def test_cada_cts_tem_os_quatro_componentes_certos(cen_on):
    for c in cen_on.cts_ids:
        obras = [o for o in cen_on.obras.values() if o.no == c and o.eh_aegea()]
        cods = {codigo(o.id) for o in obras}
        assert cods == CTS_COMPONENTES, f"CTS {c}: esperado {CTS_COMPONENTES}, veio {cods}"
        coletas = [o for o in obras if o.tipo == "coleta"]
        assert len(coletas) == 1 and codigo(coletas[0].id) == "cts", \
            "a ancora de coleta da CTS deve ser o Coletor de tempo seco"


# ---------------------------------------------------------------- os dois cenarios
#
# ESTE BLOCO AFIRMAVA IGUALDADE, e a igualdade caiu junto com a soma. Ligado e desligado
# eram "a mesma demanda" porque a linha da CTS era somada na sub-bacia — e somar conserva
# tudo. Hoje a unica diferenca para a sub-bacia e QUAL COLUNA E LIDA: com o coletor, a
# `*_com_cts` (o que sobra para ela); sem ele, a sem sufixo (a sub-bacia inteira). Sao
# dois cenarios diferentes, e nao duas contas do mesmo cenario.
def test_desligado_atende_menos_que_ligado(res_on, res_off):
    # Sem o coletor, a area que so ele alcancava fica sem atendimento. Nesta fixture, que
    # nao tem as colunas consolidadas, some a demanda inteira da CTS.
    assert cobertura_fim(res_off) < cobertura_fim(res_on)


def test_vazao_NAO_e_mais_somada(cen_on, cen_off):
    """A vazao e DADO da sub-bacia, e o motor nao a inventa para o cenario sem coletor.

    Este teste afirmava o contrario — que as duas visoes tinham a mesma vazao — e era
    verdade porque a linha da CTS era somada. Somar tambem contava a area sobreposta
    duas vezes, e misturava moedas: as ligacoes vinham da coluna consolidada e a vazao
    de uma soma.

    O CUSTO ESTA DECLARADO: sem o coletor, a vazao que chega a ETE e a que estiver na
    base da sub-bacia. Se desligar a CTS muda a vazao dela, quem atualiza a base e quem
    cadastra — o motor avisa que esse cenario existe, mas nao arbitra o numero.
    """
    assert sum(cen_off.vazao.values()) < sum(cen_on.vazao.values())
    # A diferenca e exatamente a vazao das CTS, que deixou de ser absorvida.
    _cts = sum(cen_on.vazao.get(c, 0.0) for c in cen_on.cts_ids)
    assert sum(cen_on.vazao.values()) - sum(cen_off.vazao.values()) == pytest.approx(_cts)


def test_universo_efetivo_do_desligado_e_o_da_sub_bacia(cen_on, cen_off):
    # ON:  b1 1000 + b2 900 + cts1 500x1,2  |  b3 800 + b4 1200 + cts2 400x1,5 = 5100
    # OFF: so as sub-bacias, com o potencial de cada uma  = 1000+900+800+1200   = 3900
    #
    # O potencial da CTS nao entra em media nenhuma: ele e dela, e ela nao esta na rodada.
    assert sum(cen_on.max_lig.values()) == pytest.approx(5100.0)
    assert sum(cen_off.max_lig.values()) == pytest.approx(3900.0)


# ---------------------------------------------------------------- diferencas (TEM de diferir)
def test_ligado_tem_quatro_obras_a_mais_por_cts(cen_on, cen_off):
    n_on = sum(1 for o in cen_on.obras.values() if o.eh_aegea())
    n_off = sum(1 for o in cen_off.obras.values() if o.eh_aegea())
    assert n_on - n_off == 4 * len(cen_on.cts_ids)


def test_capex_ligado_maior_e_diferenca_e_o_capex_das_obras_cts(cen_on, cen_off, res_on, res_off):
    cap_on = capex_total(cen_on, res_on)
    cap_off = capex_total(cen_off, res_off)
    assert cap_on > cap_off, "o modo ligado paga as obras dedicadas da CTS"
    capex_cts = sum(o.capex for o in cen_on.obras.values()
                    if getattr(cen_on.nos.get(o.no), "is_cts", False))
    assert cap_on - cap_off == pytest.approx(capex_cts), \
        "a diferenca de CAPEX tem de ser exatamente o CAPEX das obras da CTS"


def test_desligado_paga_menos_CAPEX_e_a_receita_segue_o_ticket_de_quem_atende(
        cen_on, cen_off, res_on, res_off):
    """Desligar a CTS nao e so economizar obra: muda quem cobra.

    Este teste afirmava `vpl_off > vpl_on`, com a justificativa "mesma receita e
    cobertura, menos CAPEX". A premissa caiu junto com a soma: a receita da linha da
    CTS nao e mais herdada pela sub-bacia.

    Sem o coletor, as ligacoes que ele atenderia passam a ser ligadas pelas obras da
    sub-bacia — e cobradas pelo TICKET DELA. Na fixture, `cts2` fatura 480 por ligacao e
    a `b4` que a absorve, 288: o plano desligado liga a mesma gente por menos dinheiro.

    O que continua valendo sempre e o CAPEX. O VPL depende de qual ticket e maior, e
    fixar um sentido aqui seria fixar um acidente desta fixture.
    """
    assert capex_total(cen_off, res_off) < capex_total(cen_on, res_on)
    _t_cts = 90000 / 200      # cts1: mesmo ticket da b1 que a absorve
    _t_b1 = 180000 / 400
    assert _t_cts == pytest.approx(_t_b1), "a fixture mudou; reveja o raciocinio acima"


# ---------------------------------------------------------------- retrocompatibilidade
def test_banco_sem_cts_modos_sao_identicos():
    # base fixa SEM CTS -> usar_cts ligado ou desligado tem de dar exatamente o mesmo
    a = load_fixture(usar_cts=True)
    b = load_fixture(usar_cts=False)
    assert not a.cts_ids and not b.cts_ids, "o fixture nao deve ter CTS"
    assert set(a.nos) == set(b.nos)
    assert set(a.obras) == set(b.obras)


# ---------------------------------------------------------------- as duas colunas
#
# A SEMANTICA E A DA PLANILHA DO DATABRICKS (conferida em 09/2026): a coluna sem sufixo e
# a sub-bacia INTEIRA, sem considerar a CTS; a `*_com_cts` e a sub-bacia com a CTS
# considerada a parte — so o que nao e area do coletor, e VAZIA quando o coletor levou
# tudo. A sobreposicao e contada uma vez: na CTS quando ela existe (`usar_cts=True`, e a
# sub-bacia le `_com_cts`), na sub-bacia quando nao existe (sem sufixo).
#
# Esta fixture NAO tem as colunas `_com_cts` — e o caminho de compatibilidade, em que a
# sub-bacia entra inteira nos dois modos e o motor ALERTA que, com o coletor, a area
# dele conta duas vezes. Os testes abaixo acrescentam as colunas a mao.

from _helpers import BANK_CTS, banco, silent


def _com_colunas(por_sub):
    """As abas do banco de CTS com as colunas `*_com_cts` de `{sub: {coluna: valor}}`.

    So a sub-bacia nomeada recebe a coluna. As outras ficam SEM ela, e e de proposito: e
    assim que o banco chega enquanto a carga nao preenche todas — o motor tem de saber
    cair na coluna sem sufixo quando a `_com_cts` nem existe na linha."""
    abas = banco(BANK_CTS)
    for linha in abas["subbacia-operacional"]:
        valores = por_sub.get(linha.get("sub_bacia"))
        if valores:
            linha.update(valores)
    return abas


#: b1 tem 1.000 ligacoes inteira; com a cts1 a parte sobram 800 (200 sao area do coletor).
#: b4 tem 1.200; com a cts2 a parte sobram 1.050. A receita encolhe junto.
COM_CTS = {
    "b1": {"universo_ligacoes_com_cts": 800, "ligacoes_atuais_com_cts": 350,
           "universo_economias_com_cts": 880, "economias_atuais_com_cts": 385,
           "receita_faturada_media_mensal_com_cts": 160000,
           "receita_arrecadada_media_mensal_com_cts": 144000},
    "b4": {"universo_ligacoes_com_cts": 1050, "ligacoes_atuais_com_cts": 450,
           "universo_economias_com_cts": 1155, "economias_atuais_com_cts": 495,
           "receita_faturada_media_mensal_com_cts": 140000,
           "receita_arrecadada_media_mensal_com_cts": 126000},
}


def test_com_cts_a_sub_bacia_le_a_coluna_com_cts():
    # b1 tem 1000 ligacoes inteira e a cts1 atende 500, das quais 200 dentro de b1. Com o
    # coletor, b1 fica com as 800 que sobram; as 200 estao na CTS. Somar 1000 + 500
    # contaria as 200 duas vezes.
    arq = _com_colunas(COM_CTS)
    M = engine()
    on = silent(M.ler_banco, arq, usar_cts=True)
    cid = on.nos["b1"].cidade
    # b1, b2 e cts1 estao na mesma cidade. O universo EFETIVO ja leva o potencial:
    #   b1 = 800 (com_cts) x 1,0 + b2 = 900 x 1,0 + cts1 = 500 x 1,2 = 2300
    assert on.max_lig[cid] == pytest.approx(2300.0)
    # A receita tambem e a `_com_cts` (base arrecadada, o padrao): 144.000 / 350.
    assert on.sub_receita["b1"]["ticket"] == pytest.approx(144000 / 350)


def test_a_area_do_coletor_e_contada_uma_vez_em_cada_cenario():
    # Com as duas sub-bacias pareadas trazendo a coluna:
    #
    #   ON   b1 800 + b2 900 + cts1 500x1,2 = 2300  |  b3 800 + b4 1050 + cts2 400x1,5 = 2450
    #   OFF  b1 1000 + b2 900               = 1900  |  b3 800 + b4 1200               = 2000
    #
    # Ligado tem mais universo porque o coletor tem potencial de crescimento e alcanca
    # area que a sub-bacia inteira nao cobre; desligado, a area do coletor e da
    # sub-bacia — e so ela.
    arq = _com_colunas(COM_CTS)
    M = engine()
    on = silent(M.ler_banco, arq, usar_cts=True)
    off = silent(M.ler_banco, arq, usar_cts=False)
    assert sum(on.max_lig.values()) == pytest.approx(4750.0)
    assert sum(off.max_lig.values()) == pytest.approx(3900.0)


def test_sem_cts_a_coluna_com_cts_e_ignorada():
    # Ela so descreve o cenario COM coletor. Sem ele, a sub-bacia e a inteira — a mesma
    # com ou sem a coluna na linha.
    arq = _com_colunas(COM_CTS)
    M = engine()
    off_com = silent(M.ler_banco, arq, usar_cts=False)
    off_sem = silent(M.ler_banco, banco(BANK_CTS), usar_cts=False)
    assert sum(off_com.max_lig.values()) == pytest.approx(sum(off_sem.max_lig.values()))
    assert off_com.sub_receita["b1"]["ticket"] == pytest.approx(180000 / 400)


def test_sem_a_coluna_a_sub_bacia_entra_inteira_e_ALERTA(capsys):
    """Sem a coluna nao ha o que ler, e o motor NAO inventa: a sub-bacia entra inteira.

    Com o coletor isso conta a area dele duas vezes — e o ALERTA diz exatamente isso: e
    um numero a mais declarado, nao um numero errado em silencio.
    """
    M = engine()
    on = M.ler_banco(banco(BANK_CTS), usar_cts=True)
    saida = capsys.readouterr().out
    assert "conta duas vezes" in saida
    cid = on.nos["b1"].cidade
    assert on.max_lig[cid] == pytest.approx(2500.0)   # 1000 (b1) + 900 (b2) + 600 (cts1)


def test_com_cts_vazia_e_coletor_por_perto_a_sub_bacia_vale_zero(capsys):
    """`_com_cts` vazia numa sub-bacia pareada e "a CTS levou tudo" — Nilopolis tem seis
    assim. Ela entra com zero, e nao com a inteira: a inteira e o coletor de novo."""
    vazia = {"b1": {k: None for k in COM_CTS["b1"]}, "b4": COM_CTS["b4"]}
    M = engine()
    on = M.ler_banco(_com_colunas(vazia), usar_cts=True)
    saida = capsys.readouterr().out
    assert "levou a area inteira" in saida
    cid = on.nos["b1"].cidade
    assert on.max_lig[cid] == pytest.approx(1500.0)   # 0 (b1) + 900 (b2) + 600 (cts1)
    assert on.sub_receita["b1"]["ticket"] == 0.0


#: A RELACAO REAL da planilha (conferida em 19/09/2026 nas 337 sub-bacias pareadas):
#: sem sufixo - com_cts = a CTS, coluna a coluna. b1 inteira 1000, cts1 500 -> 500;
#: b4 inteira 1200, cts2 400 -> 800. Ligacoes atuais e receita seguem a mesma conta.
COM_CTS_EXATA = {
    "b1": {"universo_ligacoes_com_cts": 500, "ligacoes_atuais_com_cts": 200,
           "universo_economias_com_cts": 550, "economias_atuais_com_cts": 220,
           "receita_faturada_media_mensal_com_cts": 100000,
           "receita_arrecadada_media_mensal_com_cts": 90000},
    "b4": {"universo_ligacoes_com_cts": 800, "ligacoes_atuais_com_cts": 350,
           "universo_economias_com_cts": 880, "economias_atuais_com_cts": 385,
           "receita_faturada_media_mensal_com_cts": 80000,
           "receita_arrecadada_media_mensal_com_cts": 72000},
}


def _sem_potencial(abas):
    """Potencial 1,0 em todo mundo, para o universo efetivo ser o universo cru."""
    for aba in ("subbacia-operacional", "cts-operacional"):
        for linha in abas[aba]:
            linha["potencial_crescimento"] = 1.0
    return abas


def test_a_area_do_coletor_e_contada_UMA_vez_o_universo_da_unidade_e_o_mesmo_nos_dois_modos():
    """O invariante que o bug de 09/2026 violava. Com a relacao real (sem sufixo -
    com_cts = CTS) e sem potencial de crescimento:

        OFF  b1 1000 + b2 900 + b3 800 + b4 1200                     = 3900
        ON   b1 500 + b2 900 + b3 800 + b4 800 + cts1 500 + cts2 400 = 3900

    A area do coletor esta na CTS num cenario e na sub-bacia no outro — nunca nos dois,
    nunca em nenhum."""
    M = engine()
    on = silent(M.ler_banco, _sem_potencial(_com_colunas(COM_CTS_EXATA)), usar_cts=True)
    off = silent(M.ler_banco, _sem_potencial(_com_colunas(COM_CTS_EXATA)), usar_cts=False)
    assert sum(on.max_lig.values()) == pytest.approx(3900.0)
    assert sum(off.max_lig.values()) == pytest.approx(3900.0)
    # O ticket da b1 e o da parte que sobrou para ela: 90.000 / 200 com o coletor. (Nao e
    # invariante que ele iguale o de sem coletor — a fixture reparte a receita na
    # proporcao das ligacoes, e por isso aqui coincide; na base real nao precisa.)
    assert on.sub_receita["b1"]["ticket"] == pytest.approx(90000 / 200)


def _com_cidade(abas):
    """Poe `cidade_id` nas sub-bacias E nas CTS pela topologia (a fixture nao o traz nas
    linhas operacionais; a carga real traz). cts1 fica na cidade de b1 e b2; cts2 na de
    b3 e b4."""
    sis_cid = {d["sistema_id"]: d["cidade_id"] for d in abas["cidade-sistema"]}
    comp_sis = {d["componente_sistema_id"]: d["sistema_id"] for d in abas["sistema-topologia"]}
    for aba, chave in (("subbacia-operacional", "sub_bacia"), ("cts-operacional", "cts")):
        for linha in abas[aba]:
            linha["cidade_id"] = sis_cid.get(comp_sis.get(linha[chave]))
    return abas


def test_com_os_pares_na_carga_a_cidade_NAO_zera_a_sub_bacia_sem_par(capsys):
    """b2 divide a cidade com a cts1 mas nao esta pareada com ela. A carga traz os
    pares (b1<->cts1, b4<->cts2): b2 vazia e coluna nao preenchida, e entra INTEIRA, com
    aviso. Zera-la pelo vizinho apagaria 900 ligacoes que nada tem a ver com o coletor."""
    abas = _com_colunas({**COM_CTS, "b2": {"universo_ligacoes_com_cts": None}})
    abas = _com_cidade(abas)
    M = engine()
    on = M.ler_banco(abas, usar_cts=True)
    saida = capsys.readouterr().out
    assert on.max_lig[on.nos["b2"].cidade] == pytest.approx(2300.0)   # 800 (b1) + 900 (b2) + 600 (cts1)
    assert "SEM CTS pareada" in saida and "b2" in saida


def test_o_escopo_do_par_e_a_CIDADE_e_nao_o_banco_inteiro(capsys):
    """Uma carga com par numa cidade e nenhum na outra (Rio pareado, Nilopolis so pela
    cidade). Na cidade COM par, a vazia sem par entra inteira; na cidade SEM par nenhum,
    a cidade decide e a vazia e zerada. Decidir pelo banco inteiro faria o par do Rio
    desligar o sinal de Nilopolis."""
    abas = _com_colunas({"b1": COM_CTS["b1"],
                         "b2": {"universo_ligacoes_com_cts": None},
                         "b4": {k: None for k in COM_CTS["b4"]}})
    abas = _com_cidade(abas)
    abas["subbacia-cts"] = [p for p in abas["subbacia-cts"] if p["cts"] == "cts1"]   # so c1 tem par
    M = engine()
    on = M.ler_banco(abas, usar_cts=True)
    saida = capsys.readouterr().out
    assert on.max_lig[on.nos["b2"].cidade] == pytest.approx(2300.0)   # 800 (b1) + 900 (b2 inteira) + 600 (cts1)
    assert on.max_lig[on.nos["b4"].cidade] == pytest.approx(1400.0)   # 800 (b3) + 0 (b4 zerada) + 600 (cts2)
    assert "SEM CTS pareada" in saida and "b2" in saida
    assert "sinal: cidade" in saida and "b4" in saida


def test_sem_par_nenhum_na_carga_a_cidade_decide(capsys):
    """A planilha do Databricks nao traz o par: `subbacia-cts` chega vazia. Ai a cidade e
    o unico sinal, e vale — b1 vazia na cidade da cts1 e "a CTS levou tudo"."""
    abas = _com_colunas({"b1": {k: None for k in COM_CTS["b1"]}, "b4": COM_CTS["b4"]})
    abas = _com_cidade(abas)
    abas["subbacia-cts"] = []
    M = engine()
    on = M.ler_banco(abas, usar_cts=True)
    saida = capsys.readouterr().out
    assert on.max_lig[on.nos["b1"].cidade] == pytest.approx(1500.0)   # 0 (b1) + 900 (b2) + 600 (cts1)
    assert "sinal: cidade" in saida


def test_o_aviso_das_zeradas_diz_quantas_ligacoes_inteiras_elas_tinham(capsys):
    """E o numero que denuncia um zero indevido: uma sub-bacia grande zerada por engano
    aparece com o tamanho dela, em vez de sumir do plano em silencio."""
    vazia = {"b1": {k: None for k in COM_CTS["b1"]}, "b4": COM_CTS["b4"]}
    M = engine()
    M.ler_banco(_com_colunas(vazia), usar_cts=True)
    saida = capsys.readouterr().out
    assert "1 sub-bacia(s)" in saida and "1,000 ligacoes inteiras" in saida
    assert "('b1', 1000)" in saida and "sinal: par" in saida


def test_com_cts_vazia_SEM_coletor_por_perto_fica_a_inteira():
    """b2 nao esta pareada e nao ha CTS na cidade dela (a fixture nao tem cidade nas
    CTS): a coluna vazia e so coluna nao preenchida, e vale a sem sufixo."""
    vazia = {"b2": {"universo_ligacoes_com_cts": None}}
    M = engine()
    on = silent(M.ler_banco, _com_colunas(vazia), usar_cts=True)
    cid = on.nos["b2"].cidade
    assert on.max_lig[cid] == pytest.approx(2500.0)   # 1000 (b1) + 900 (b2) + 600 (cts1)


def test_as_novas_derivam_das_colunas_lidas():
    """`*_novas_obras` e derivado (universo - atuais) das colunas que ficaram na linha:
    b1 tem 600 na linha inteira (1000 - 400); com o coletor sobram 800 - 350 = 450 para
    as obras dela habilitarem, e o resto e da CTS."""
    M = engine()
    on = silent(M.ler_banco, _com_colunas(COM_CTS), usar_cts=True)
    lig_b1 = next(o for o in on.obras.values() if o.no == "b1" and o.tipo == "coleta")
    assert lig_b1.lig == pytest.approx(450.0)
    off = silent(M.ler_banco, _com_colunas(COM_CTS), usar_cts=False)
    lig_b1 = next(o for o in off.obras.values() if o.no == "b1" and o.tipo == "coleta")
    assert lig_b1.lig == pytest.approx(600.0)
