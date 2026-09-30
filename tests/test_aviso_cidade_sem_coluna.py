"""CIDADE SEM NENHUMA COLUNA CANDIDATA PASSA A APARECER.

O mestre do solver por decomposição escolhe **exatamente uma** coluna por cidade
(`AddExactlyOne`). Se a única que sobrou for a "nada", a cidade não entra em plano nenhum —
e na tela isso é indistinguível de "não valeu a pena".

É o modo de falha mais caro que este solver tem, porque `_colunas_faseada` descarta em
SILÊNCIO toda coluna que `viavel()` recusa (`if not M.viavel(sub,pl)[0]: continue`). Foi
assim que três cidades da uB2 — Cabo Frio, Nilópolis e Pinheiral — ficaram fora de qualquer
plano, com 205 sub-bacias e 65.930 ligações novas, por um defeito no agendamento da expansão
da ETE. Nenhuma linha de log, em lugar nenhum.

Pedido da revisão de produção do Codex (30/09/2026), com a mudança pronta para subir na
Azure: o acesso é só por VPN, o deploy do motor é separado do serviço, e **o que não se vê
não se conserta**. Agora a pergunta "por que esta cidade não tem obra?" se responde por SQL
em `controle.run_diagnostico`.
"""
import pytest

from _helpers import engine, silent, solver_or_skip
from test_ete_nova_expande import _abas, _cen, _plano_tudo

#: O construtor ATUAL, guardado antes de qualquer troca nos testes.
_NOVA = None


def _guardar_atual():
    global _NOVA
    if _NOVA is None:
        _NOVA = solver_or_skip()._montar_faseado
    return _NOVA


#: O CONSTRUTOR DE PLANOS ANTERIOR A 29/09/2026 — o de `3f9d558^`, palavra por palavra.
#:
#: Ele agendava a expansão no mesmo mês do pacote, e a precedência recusa. É com ele que a
#: recusa de coluna acontece de verdade, e é dele que sai o MOTIVO que o aviso carrega.
def _montar_faseado_antigo(sub, built, shift_meses=0):
    import math
    pl = {oid: None for oid in sub.obras}
    for oid, o in sub.obras.items():
        if o.no in built and o.eh_aegea() and o.tipo != "ete_mod":
            _py = getattr(o, "_obrig_planyear", None)
            if _py is not None:
                pl[oid] = min(max(int(o.inicio_min), (_py - 1) * 12), _py * 12 - 1)
            else:
                pl[oid] = o.inicio_min + shift_meses
    for sis, mm in getattr(sub, "modulos_sis", {}).items():
        e = sub.ete_do_sistema.get(sis)
        tot = sum(sub.vazao.get(sb, 0.0) for sb in built if sub.nos[sb].sistema == sis)
        need = (int(math.ceil(max(0.0, tot - e.folga) / e.cap_modulo))
                if (e and e.cap_modulo > 0) else (1 if (e and tot > e.folga) else 0))
        for k, mo in enumerate(mm):
            pl[mo.id] = (mo.inicio_min + shift_meses) if k < need else None
    return pl


#: A CIDADE QUE FICA SEM OPÇÃO, forçada no gerador de colunas.
#:
#: Aqui se testa o ALARME, e não o incêndio. A causa real — colunas recusadas pela
#: precedência e descartadas em silêncio — tem teste próprio
#: (`test_a_ETE_NOVA_com_expansao_NAO_perde_as_colunas_candidatas`), e já está corrigida;
#: reproduzi-la aqui exigiria uma cidade cujo ÚNICO sistema tem ETE nova, e a fixture da
#: suíte tem mais de um sistema por cidade. O que este arquivo precisa garantir é que,
#: QUANDO o estado acontecer, ele apareça.
#:
#: A "nada" é a coluna que não constrói obra alguma — `_colunas_faseada` sempre a emite
#: primeiro, e é ela que sobra quando todas as outras são recusadas.
def _so_a_coluna_nada(CP, cidade_alvo):
    original = CP._colunas_sistema

    def dentro(cen, cid, **kw):
        cols = original(cen, cid, **kw)
        if str(cid) != str(cidade_alvo):
            return cols
        nada = [c for c in cols if all(v is None for v in c[2].values())]
        assert nada, "o gerador sempre emite a coluna 'nada'"
        return nada[:1]

    return dentro


@pytest.fixture
def cenario_sem_coluna(monkeypatch):
    """Uma cidade com obra disponível e nenhuma coluna que construa algo."""
    from _helpers import ORC_SLACK
    CP = solver_or_skip()
    cen = _cen(_abas(modulos=1), ete_faseada=True, orcamento=ORC_SLACK)
    alvo = sorted({n.cidade for n in cen.nos.values()})[0]
    monkeypatch.setattr(CP, "_colunas_sistema", _so_a_coluna_nada(CP, alvo))
    return cen, alvo


def test_o_solver_DENUNCIA_a_cidade_sem_coluna(cenario_sem_coluna):
    CP = solver_or_skip()
    cen, alvo = cenario_sem_coluna
    res = silent(CP.resolver_por_sistema, cen, max_time_s=20, workers=2)
    sem = res.get("cidades_sem_coluna_viavel")
    assert sem == [alvo], f"esperava só {alvo}, veio {sem}"
    assert res.get("aviso_colunas"), "o aviso em texto, para o log e o diagnóstico"
    assert "não é decisão econômica" in res["aviso_colunas"].replace("nao", "não") \
        or "nao e decisao economica" in res["aviso_colunas"], res["aviso_colunas"]
    # e nenhuma obra da cidade entrou no plano, que é o efeito que o aviso explica
    assert not any(v is not None for oid, v in res["plano"].items()
                   if cen.obras[oid].eh_aegea() and cen.cidade_da(cen.obras[oid]) in sem)


def test_quando_TODA_cidade_tem_opcao_a_lista_sai_vazia():
    """O guarda: o aviso não pode disparar em rodada sadia, senão vira ruído e ninguém
    olha mais para ele."""
    from _helpers import ORC_SLACK
    CP = solver_or_skip()
    cen = _cen(_abas(modulos=1), ete_faseada=True, orcamento=ORC_SLACK)
    res = silent(CP.resolver_por_sistema, cen, max_time_s=20, workers=2)
    assert res.get("cidades_sem_coluna_viavel") == []
    assert not res.get("aviso_colunas")


def test_o_PORTAO_DE_QUALIDADE_grava_o_aviso(cenario_sem_coluna):
    """É por `run_diagnostico` que a resposta chega a quem opera — em rede fechada, é a
    única trilha que sobra depois de o log do driver do Databricks se perder."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.dominio import qualidade as Q
    from otimizador.infraestrutura import persistencia as P
    CP = solver_or_skip()
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen, alvo = cenario_sem_coluna
    res = silent(CP.resolver_por_sistema, cen, max_time_s=20, workers=2)
    tabs = silent(P.materializar, cen, silent(M.avaliar, cen, res["plano"]),
                  run_id="run_sem_coluna", banco="pg")
    ok, rel, _resumo = Q.checar(cen, res, tabs)
    linha = next(l for l in rel if l["check"].startswith("Colunas candidatas"))
    assert linha["nivel"] == "aviso", "não pode bloquear: ficar sem coluna pode ser legítimo"
    assert not linha["ok"], "e tem de estar marcada como não-ok, senão ninguém a lê"
    for cid in res["cidades_sem_coluna_viavel"][:1]:
        assert str(cid) in linha["detalhe"], linha["detalhe"]


def test_o_aviso_NAO_bloqueia_a_publicacao(cenario_sem_coluna):
    """Ele é diagnóstico, não portão: uma rodada com cidade sem opção continua publicável,
    porque o número dela está certo — o que faltava era dizer POR QUE."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.dominio import qualidade as Q
    from otimizador.infraestrutura import persistencia as P
    CP = solver_or_skip()
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen, alvo = cenario_sem_coluna
    res = silent(CP.resolver_por_sistema, cen, max_time_s=20, workers=2)
    tabs = silent(P.materializar, cen, silent(M.avaliar, cen, res["plano"]),
                  run_id="run_sem_coluna2", banco="pg")
    ok, rel, _ = Q.checar(cen, res, tabs)
    criticos_falhos = [l for l in rel if l["nivel"] == "critico" and not l["ok"]]
    assert not any(l["check"].startswith("Colunas candidatas") for l in criticos_falhos)


# ------------------------------------------ o MOTIVO do descarte (revisão 6 do Codex)
#
# Ele confirmou que o aviso por cidade não pega falha PARCIAL: cidade que perdeu quase
# todas as colunas, mas guardou uma minúscula, sai da lista. E a saída óbvia — olhar por
# sistema, em vez de por cidade — tem um problema pior: "nenhuma coluna constrói este
# sistema" também acontece quando o sistema não valia a pena, e tratar economia como falha
# seria ruído.
#
# O que distingue os dois é o MOTIVO, e ele só existe onde a recusa acontece. Então o
# diagnóstico passou a ser contado dentro de `_colunas_faseada`: quantas colunas foram
# testadas, quantas recusadas, por quê, e quantas ficaram fora da janela de CAPEX.
def test_o_diagnostico_conta_o_que_foi_TESTADO_e_ACEITO(cenario_sem_coluna):
    CP = solver_or_skip()
    cen, alvo = cenario_sem_coluna
    res = silent(CP.resolver_por_sistema, cen, max_time_s=20, workers=2)
    diag = res.get("diag_colunas") or {}
    assert diag, "o diagnóstico tem de existir em rodada faseada"
    d = diag[alvo]
    assert d["testadas"] > 0, d
    assert d["testadas"] >= d["aceitas"] + d["recusadas"] + d["repetidas"], d
    assert "sub_bacias_no_conjunto" in d


def test_o_AVISO_diz_o_MOTIVO_e_nao_so_o_fato():
    """Aviso que diz "algo deu errado" deixa a investigação inteira para quem lê.

    Com o construtor anterior a 29/09, a recusa é a precedência da expansão — e é esse
    texto que tem de chegar a quem opera, com a contagem.
    """
    from _helpers import ORC_SLACK
    CP = solver_or_skip()
    cen = _cen(_abas(modulos=1), ete_faseada=True, orcamento=ORC_SLACK)
    _guardar_atual()
    CP._montar_faseado = _montar_faseado_antigo
    try:
        res = silent(CP.resolver_por_sistema, cen, max_time_s=20, workers=2)
    finally:
        CP._montar_faseado = _NOVA
    recusadas = res.get("colunas_recusadas") or 0
    assert recusadas > 0, "o construtor antigo tem de ter coluna recusada"
    motivos = {m for d in res["diag_colunas"].values() for m in d["motivos"]}
    assert any("antes de a ETE nova ficar pronta" in m for m in motivos), motivos


def test_o_PORTAO_publica_a_contagem_mesmo_quando_PASSA():
    """O número em si é a informação: ele é a base de comparação da próxima rodada, e é o
    que mostra cidade que entrou no plano com menos opção do que devia."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.dominio import qualidade as Q
    from otimizador.infraestrutura import persistencia as P
    from _helpers import ORC_SLACK
    CP = solver_or_skip()
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen = _cen(_abas(modulos=1), ete_faseada=True, orcamento=ORC_SLACK)
    res = silent(CP.resolver_por_sistema, cen, max_time_s=20, workers=2)
    tabs = silent(P.materializar, cen, silent(M.avaliar, cen, res["plano"]),
                  run_id="run_diag", banco="pg")
    _ok, rel, _ = Q.checar(cen, res, tabs)
    linha = next(l for l in rel if l["check"].startswith("Colunas candidatas: quantas"))
    assert linha["nivel"] == "aviso" and linha["ok"], "é informação, não reprovação"
    assert "recusadas em" in linha["detalhe"], linha["detalhe"]


def test_rodada_NAO_faseada_nao_finge_diagnostico():
    """Sem o caminho faseado não há geração de colunas, e inventar contagem zero pareceria
    "nada foi recusado" onde o certo é "não se mediu"."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.dominio import qualidade as Q
    from otimizador.infraestrutura import persistencia as P
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen = _cen(_abas(modulos=1))
    #: o `res` COMPLETO da avaliação, e não um dicionário de mentira: o portão lê VPL,
    #: cobertura e o resto dele, e um `res` recortado testaria outra coisa.
    res = silent(M.avaliar, cen, _plano_tudo(cen))
    tabs = silent(P.materializar, cen, res, run_id="run_sem_diag", banco="pg")
    _ok, rel, _ = Q.checar(cen, res, tabs)
    linha = next(l for l in rel if l["check"].startswith("Colunas candidatas: quantas"))
    assert "sem diagnostico" in linha["detalhe"], linha["detalhe"]
