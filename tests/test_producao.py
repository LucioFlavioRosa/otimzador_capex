"""Camada de PRODUCAO (job / portao de qualidade) — as regras que a revisao corrigiu e
que nao podem voltar a quebrar. Nao toca no motor nem nos valores golden.

Nenhum destes testes precisa de Postgres: o que se testa aqui e traducao de parametros e
logica do portao, ambos Python puro.
"""
import inspect

import pandas as pd
import pytest

from _helpers import engine

import job_databricks as J
import qualidade as Q


# --------------------------------------------------------- traducao de parametros
def test_todo_kwarg_do_mapa_existe_no_ler_banco():
    """Se alguem renomear um parametro do ler_banco, o job tem de quebrar AQUI e nao em
    producao, silenciosamente ignorando o parametro."""
    aceitos = set(inspect.signature(engine().ler_banco).parameters)
    for chave, kw in J.MAPA_PARAMS.items():
        assert kw in aceitos, f"{chave} -> {kw} nao e parametro de ler_banco"


def test_chave_ausente_nao_vira_default_do_job():
    """A regressao mais cara da revisao: o job tinha ete_faseada=True (motor: False) e
    foco_cobertura=1.0 (motor: None). foco_cobertura=1.0 satura o peso de cobertura, ou
    seja, uma run_request sem essa chave rodava 'so cobertura' em vez de 'so VPL'."""
    kw = J._params_para_ler_banco({"ORCAMENTO": 1000.0})
    assert kw == {"orcamento": 1000.0}, "o job nao pode inventar default proprio"
    assert "ete_faseada" not in kw and "foco_cobertura" not in kw


def test_chave_desconhecida_e_erro():
    """`orcamento` minusculo passava batido e a rodada saia sem teto de CAPEX."""
    with pytest.raises(ValueError, match="desconhecidas"):
        J._params_para_ler_banco({"orcamento": 1000.0})


def test_chaves_do_job_nao_viram_kwarg_do_motor():
    kw = J._params_para_ler_banco({"ORCAMENTO": 1.0, "USUARIO": "x", "MAX_TIME_S": 60})
    assert set(kw) == {"orcamento"}


def test_traduz_todas_as_chaves_conhecidas():
    p = {chave: i for i, chave in enumerate(J.MAPA_PARAMS)}
    kw = J._params_para_ler_banco(p)
    assert set(kw) == set(J.MAPA_PARAMS.values())


# ------------------------------------------------- orcamento vindo de JSONB
def test_orcamento_por_ano_vira_chave_int():
    """JSONB devolve chave de objeto como STRING, mas o motor so reconhece o cronograma
    por ano se as chaves forem int (`all(isinstance(k,int) ...)` em ler_banco). Sem a
    conversao, {"2026": ...} cai no ramo 'orcamento por unidade', nao acha a unidade e o
    teto vira INF."""
    kw = J._params_para_ler_banco({"ORCAMENTO": {"2026": 50e6, "2027": 40e6}})
    assert kw["orcamento"] == {2026: 50e6, 2027: 40e6}
    assert all(isinstance(k, int) for k in kw["orcamento"])


def test_orcamento_por_ano_e_reconhecido_como_cronograma_pelo_motor():
    """O teste acima checa o formato; este checa que o MOTOR concorda — replica a
    condicao real de `ler_banco` para nao depender de eu ter lido certo."""
    kw = J._params_para_ler_banco({"ORCAMENTO": {"2026": 50e6, "2027": 40e6}})
    orc = kw["orcamento"]
    e_cronograma = (isinstance(orc, dict) and len(orc) > 0
                    and all(isinstance(k, int) and 1900 <= k <= 2200 for k in orc))
    assert e_cronograma, "o motor nao reconheceria isto como cronograma por ano"


def test_orcamento_por_unidade_nao_e_convertido():
    """Chave que nao e ano fica intacta — e orcamento por unidade/regional."""
    kw = J._params_para_ler_banco({"ORCAMENTO": {"u1": 50e6, "u2": 40e6}})
    assert kw["orcamento"] == {"u1": 50e6, "u2": 40e6}


@pytest.mark.parametrize("valor", [50e6, None, {"2026": 1, "u1": 2}])
def test_orcamento_em_outros_formatos_passa_intacto(valor):
    """Numero, None e dict misto (que o motor tambem nao trata como cronograma)."""
    assert J._params_para_ler_banco({"ORCAMENTO": valor})["orcamento"] == valor


# ------------------------------------------------------ teto anual de CAPEX
class _CenFalso:
    def __init__(self, orc):
        self.orc = orc


def test_exigir_teto_anual_aceita_teto_finito():
    J._exigir_teto_anual(_CenFalso({"reg1": [50e6, 40e6, 0.0]}))     # 0 e teto, nao ausencia


def test_exigir_teto_anual_recusa_infinito():
    """ORCAMENTO_TOTAL sozinho deixa cen.orc em INF: ele limita o total da janela, mas a
    restricao ANUAL do CP-SAT continua lendo cen.orc e estoura em int(round(inf))."""
    with pytest.raises(ValueError, match="sem teto anual"):
        J._exigir_teto_anual(_CenFalso({"reg1": [50e6, float("inf")]}))


def test_exigir_teto_anual_nomeia_a_regional_sem_teto():
    with pytest.raises(ValueError, match="reg2"):
        J._exigir_teto_anual(_CenFalso({"reg1": [1.0], "reg2": [float("inf")]}))


# ------------------------------------------------------------------- portao
def _tabs_sadias(run_id="r1"):
    """Tabelas minimas e coerentes: passam em todas as checagens criticas."""
    return {
        "run_meta": pd.DataFrame([{"run_id": run_id, "capex_total": 100.0}]),
        "run_obra": pd.DataFrame([{"run_id": run_id, "obra_id": "o1", "tipo": "coleta",
                                   "capex": 100.0, "construida": True}]),
        "run_subbacia": pd.DataFrame([{"run_id": run_id, "sub_bacia": "b1",
                                       "vpl": 50.0, "faturando": True}]),
        "run_ano": pd.DataFrame([{"run_id": run_id, "ano": 2026, "capex": 100.0,
                                  "opex": 1.0, "receita": 2.0, "teto_capex": 500.0,
                                  "excesso": 0.0}]),
        "run_cidade_ano": pd.DataFrame([{"run_id": run_id, "cidade": "c1",
                                         "ano": 2026, "capex": 100.0}]),
    }


RES_OK = {"milp_status": "OTIMO | OBRIG 0/0", "vpl": 50.0}


def test_portao_aprova_rodada_sadia():
    ok, rel, _ = Q.checar(None, RES_OK, _tabs_sadias())
    assert ok, [r for r in rel if not r["ok"] and r["nivel"] == "critico"]


@pytest.mark.parametrize("status", ["OTIMO", "OTIMO | OBRIG 3/3", "VIAVEL(limite de tempo)",
                                    "VIAVEL(limite de tempo) | so cobertura"])
def test_portao_aceita_os_status_que_o_cpsat_realmente_devolve(status):
    """O cpsat63 devolve 'OTIMO'/'VIAVEL(...)' com sufixos — nunca 'OPTIMAL'/'FEASIBLE'.
    A checagem antiga (`st in ('OPTIMAL','FEASIBLE')`) reprovava 100% das rodadas boas."""
    ok, _, _ = Q.checar(None, {**RES_OK, "milp_status": status}, _tabs_sadias())
    assert ok


def test_portao_reprova_sem_solucao():
    ok, _, _ = Q.checar(None, {**RES_OK, "milp_status": "SEM SOLUCAO(3)"}, _tabs_sadias())
    assert not ok


def test_portao_reprova_tabela_obrigatoria_vazia():
    t = _tabs_sadias()
    t["run_cidade_ano"] = t["run_cidade_ano"].head(0)
    ok, rel, _ = Q.checar(None, RES_OK, t)
    assert not ok
    assert any("obrigatorias" in r["check"] and not r["ok"] for r in rel)


def test_portao_reprova_run_id_divergente():
    t = _tabs_sadias()
    t["run_obra"] = t["run_obra"].assign(run_id="OUTRO")
    ok, rel, _ = Q.checar(None, RES_OK, t)
    assert not ok
    assert any(r["check"].startswith("run_id") and not r["ok"] for r in rel)


def test_portao_reprova_duplicata_de_pk():
    t = _tabs_sadias()
    t["run_obra"] = pd.concat([t["run_obra"], t["run_obra"]], ignore_index=True)
    ok, rel, _ = Q.checar(None, RES_OK, t)
    assert not ok
    assert any("duplicatas" in r["check"] and not r["ok"] for r in rel)


def test_portao_reprova_capex_sem_teto():
    """Sem orcamento o motor usa INF; a checagem de estouro passa trivialmente."""
    t = _tabs_sadias()
    t["run_ano"] = t["run_ano"].assign(teto_capex=float("inf"))
    ok, rel, _ = Q.checar(None, RES_OK, t)
    assert not ok
    assert any("teto definido" in r["check"] and not r["ok"] for r in rel)


# ------------------------------------------------------- run_id da rodada
def test_materializar_respeita_o_run_id_da_rodada():
    """Sem `run_id=` a materializacao gera um id novo: controle.* e public.otim_* deixam
    de casar e cada retry publica de novo em vez de substituir."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    import dashboard_otimizador_v2 as D
    import persistencia as P
    from _helpers import load_cts, build_all, silent
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen = load_cts(True)
    tabs = silent(P.materializar, cen, build_all(cen), run_id="run_fixo_123", banco="pg")
    for nome, df in tabs.items():
        if nome.startswith("snapshot__") or "run_id" not in df.columns:
            continue
        assert set(df["run_id"].unique()) <= {"run_fixo_123"}, nome
