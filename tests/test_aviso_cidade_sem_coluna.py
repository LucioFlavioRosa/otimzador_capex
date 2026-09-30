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
from test_ete_nova_expande import _abas, _cen


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
