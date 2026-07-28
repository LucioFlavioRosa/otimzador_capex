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
        # os nomes destas duas colunas importam: o portao procurava `deficit` e
        # `cobertura`, que NAO existem — as checagens ficavam mudas (ver test abaixo).
        "run_meta_cobertura": pd.DataFrame([{"run_id": run_id, "cidade": "c1", "ano": 2026,
                                             "deficit_ligacoes": 0.0, "atingida": True}]),
        "run_cobertura": pd.DataFrame([{"run_id": run_id, "cidade": "c1", "ano": 2026,
                                        "cobertura_pct": 80.0}]),
        # sem estas duas, as checagens de CAPEX mensal e de rateio nao rodam — e o
        # conjunto deixaria de exercitar as 14 criticas.
        "run_mes": pd.DataFrame([{"run_id": run_id, "mes_indice": 0, "ano": 2026,
                                  "capex_mes": 100.0}]),
        "run_dependencia": pd.DataFrame([{"run_id": run_id, "obra_id": "o1",
                                          "sub_bacia": "b1", "fracao_rateio": 1.0}]),
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


def test_portao_reprova_deficit_de_meta_negativo():
    """Esta checagem existia mas NUNCA rodava: procurava a coluna `deficit`, e
    persistencia grava `deficit_ligacoes`. Como as checagens sao condicionais a existencia
    da coluna, ela silenciava — o relatorio vinha com uma checagem a menos e ninguem via."""
    t = _tabs_sadias()
    t["run_meta_cobertura"] = t["run_meta_cobertura"].assign(deficit_ligacoes=-50.0)
    ok, rel, _ = Q.checar(None, RES_OK, t)
    assert not ok
    assert any("deficit" in r["check"].lower() and not r["ok"] for r in rel)


def test_portao_reprova_cobertura_negativa():
    """Mesma historia: procurava `cobertura`, a coluna e `cobertura_pct`."""
    t = _tabs_sadias()
    t["run_cobertura"] = t["run_cobertura"].assign(cobertura_pct=-1.0)
    ok, rel, _ = Q.checar(None, RES_OK, t)
    assert not ok
    assert any("Cobertura" in r["check"] and not r["ok"] for r in rel)


CHECAGENS_CRITICAS = {
    "Materializacao: tabelas obrigatorias presentes",
    "run_id: unico em todas as tabelas",
    "Chaves: sem duplicatas nas PKs",
    "Status do solver",
    "VPL: soma por sub-bacia = VPL do plano",
    "CAPEX: run_ano = run_meta",
    "CAPEX: run_mes = run_ano",
    "CAPEX: run_cidade_ano = run_ano",
    "Rateio: fracoes somam 1 por obra",
    "Orcamento: teto anual respeitado",
    "Orcamento: teto definido em todos os anos",
    "Integridade: colunas-chave sem NaN",
    "Metas: deficit nao-negativo",
    "Cobertura: valores nao-negativos",
}


def test_portao_roda_todas_as_checagens_criticas():
    """Trava os NOMES, nao um numero: checagem nova e bem-vinda (o teste diz qual apareceu),
    mas checagem que SOME em silencio — porque voltou a procurar coluna inexistente — e
    acusada com o nome dela. Foi assim que 'deficit' e 'cobertura' passaram despercebidas."""
    _, rel, _ = Q.checar(None, RES_OK, _tabs_sadias())
    executadas = {r["check"] for r in rel if r["nivel"] == "critico"}
    assert not (CHECAGENS_CRITICAS - executadas), \
        f"checagens que deixaram de rodar: {sorted(CHECAGENS_CRITICAS - executadas)}"
    novas = executadas - CHECAGENS_CRITICAS
    assert not novas, f"checagens novas — confirme e adicione a CHECAGENS_CRITICAS: {sorted(novas)}"


# ------------------------------------------------- notificacao pos-commit
def test_falha_de_notificacao_nao_derruba_a_publicacao(monkeypatch):
    """A notificacao roda DEPOIS do commit. Se ela levantasse, a excecao subiria ate o
    `except` de rodar(), que marcaria ERRO por cima de um SUCESSO ja gravado — com os dados
    publicados e visiveis. O operador reprocessaria uma rodada intacta."""
    import publicacao as PUB

    def explode(*a, **k):
        raise RuntimeError("fila fora do ar")

    monkeypatch.setattr(PUB, "notificar_service_bus", explode)
    monkeypatch.setattr(PUB, "notificar_webhook", explode)
    tabs = _tabs_sadias()
    tabs["run_meta"] = tabs["run_meta"].assign(data_hora="2026-07-27T00:00:00")
    # pg=None e blob=None: exercita so o trecho de notificacao
    pay = PUB.publicar(tabs, pg=None, blob=None, verbose=False,
                       notificar={"service_bus": "sb://x", "fila": "q",
                                  "webhook": "https://x", "token": "t"})
    assert pay["run_id"] == "r1"


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


# --------------------------------------------- snapshot do cadastro (blob)
def test_materializar_gera_snapshot_do_arquivo_fonte():
    """O job rotula a origem como 'postgres://input', que nao existe em disco. Sem
    `arquivo_fonte`, `os.path.exists(banco)` e falso e a rodada sai SEM snapshot__*:
    o blob receberia as run_* mas nao a copia congelada do cadastro, e "refazer a mesma
    rodada meses depois" deixaria de ser possivel."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    import dashboard_otimizador_v2 as D
    import persistencia as P
    from _helpers import BANK_CTS, load_cts, build_all, silent
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen = load_cts(True)
    res = build_all(cen)

    sem = silent(P.materializar, cen, res, run_id="r", banco="postgres://input")
    assert not [k for k in sem if k.startswith("snapshot__")]
    assert sem["run_meta"].iloc[0]["banco_md5"] is None

    com = silent(P.materializar, cen, res, run_id="r", banco="postgres://input",
                 arquivo_fonte=BANK_CTS)
    assert len([k for k in com if k.startswith("snapshot__")]) >= 10
    assert com["run_meta"].iloc[0]["banco_md5"]
    # a proveniencia continua sendo o rotulo, nao o caminho do arquivo temporario
    assert com["run_meta"].iloc[0]["banco_arquivo"] == "postgres://input"


# ------------------------------------------- tabela obrigatoria mas vazia
def test_tabela_obrigatoria_vazia_e_erro(monkeypatch, tmp_path):
    """Tabela VAZIA nao e o mesmo que ausente. Um metas_cobertura existente porem vazio
    (carga interrompida, TRUNCATE indevido) produziria um Cenario sem metas, que resolve,
    passa no portao e publica SUCESSO."""
    import pandas as pd
    import carregar_postgres as C

    def _com_vazia(nome_tabela):
        def read_sql_falso(sql, con, **kw):
            vazia = nome_tabela in str(sql)
            return pd.DataFrame() if vazia else pd.DataFrame([{"a": 1}])
        return read_sql_falso

    monkeypatch.setattr(C, "_engine_sqlalchemy", lambda url: _EngineFalsa())
    monkeypatch.setattr(pd, "read_sql", _com_vazia("subbacia_operacional"))
    with pytest.raises(RuntimeError, match="VAZIA"):
        C.snapshot_input_para_xlsx("postgresql://x", str(tmp_path / "s.xlsx"))


def test_tabela_tolerada_vazia_apenas_avisa(monkeypatch, tmp_path, capsys):
    """`fator_esgoto` vazio e legitimo: o motor cai em paridade 1.0 quando a cidade nao tem
    faixas (otimizador_capex_v62.py:365). Barrar aqui impediria uma rodada valida — mas
    passar em silencio esconderia uma carga interrompida. Entao: avisa."""
    import pandas as pd
    import carregar_postgres as C

    def read_sql_falso(sql, con, **kw):
        vazia = "fator_esgoto" in str(sql)
        return pd.DataFrame() if vazia else pd.DataFrame([{"a": 1}])

    monkeypatch.setattr(C, "_engine_sqlalchemy", lambda url: _EngineFalsa())
    monkeypatch.setattr(pd, "read_sql", read_sql_falso)
    C.snapshot_input_para_xlsx("postgresql://x", str(tmp_path / "s.xlsx"))
    assert "VAZIA" in capsys.readouterr().out


class _EngineFalsa:
    def dispose(self):
        pass


# =============================================================================
#  rodar() FIM A FIM, com o Postgres substituido por dublês
#
#  Nenhum teste chamava `rodar()`. Foi assim que passaram despercebidos um `import os`
#  ausente (NameError so em execucao — py_compile nao pega) e um `arquivo_fonte=` esquecido.
#  Estes testes exercitam a ORQUESTRACAO: a ordem dos passos, o que e passado a quem, e o
#  ciclo de vida do snapshot. O banco inteiro e dublado.
# =============================================================================
class _EspiaoPublicacao:
    """Substitui `publicacao` no job e grava o que foi chamado."""

    def __init__(self):
        self.status = []
        self.publicado = None
        self.diagnostico = None

    def marcar_status_controle(self, pg, run_id, status, erro=None, schema="controle"):
        self.status.append(status)

    def gravar_diagnostico(self, pg, run_id, relatorio, schema="controle"):
        self.diagnostico = relatorio

    def publicar(self, tabs, **kw):
        self.publicado = (tabs, kw)
        return {"run_id": tabs["run_meta"]["run_id"].iloc[0]}


@pytest.fixture
def job_dublado(monkeypatch):
    """Prepara `rodar()` para rodar sem banco: run_request e carga vêm da fixture."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    # O banco e a publicacao sao dublados, mas o SOLVER nao — `rodar()` chama
    # `CP.resolver_por_sistema` de verdade, que e o ponto: exercitar a orquestracao
    # inteira. Sem OR-Tools estes 4 testes FALHARIAM em vez de pular, e o CI offline
    # nasceria vermelho — contra a regra da propria doc, "nenhum skip em vermelho".
    pytest.importorskip("ortools", reason="rodar() usa o CP-SAT; OR-Tools ausente")
    from _helpers import BANK_CTS, silent
    M = engine()

    espiao = _EspiaoPublicacao()
    monkeypatch.setattr(J, "_ler_run_request",
                        lambda pg, rid, schema="controle": {"ORCAMENTO": 50e6,
                                                            "USUARIO": "teste"})

    vistos = {}

    def carregar_falso(pg_url, schema="input", snapshot_para=None, **params):
        # o adaptador real materializa o cadastro num xlsx; aqui copiamos a fixture para o
        # caminho pedido, que e exatamente o contrato de `snapshot_para`
        vistos["snapshot_para"] = snapshot_para
        if snapshot_para:
            import shutil
            shutil.copy(BANK_CTS, snapshot_para)
        return silent(M.ler_banco, BANK_CTS, **params)

    monkeypatch.setitem(__import__("sys").modules, "publicacao", espiao)
    monkeypatch.setattr("carregar_postgres.carregar_postgres", carregar_falso)
    return espiao, vistos


def test_rodar_fim_a_fim_publica_e_marca_status(job_dublado, capsys):
    espiao, vistos = job_dublado
    r = J.rodar("run_teste", "postgresql://dublê", max_time_s=20)
    assert r["status"] == "SUCESSO", r
    assert espiao.status == ["RODANDO"], "SUCESSO deve entrar junto com a publicacao"
    assert espiao.publicado is not None
    assert espiao.diagnostico, "o diagnostico e gravado mesmo quando a rodada passa"


def test_rodar_passa_o_snapshot_para_a_materializacao(job_dublado):
    """O bug que escapou: sem `arquivo_fonte=snap`, a rodada publica sem snapshot__* e a
    camada de reproducao fica vazia — sem nenhum erro."""
    espiao, vistos = job_dublado
    J.rodar("run_teste", "postgresql://dublê", max_time_s=20)

    assert vistos["snapshot_para"], "carregar_postgres tem de receber snapshot_para"
    tabs, _ = espiao.publicado
    snaps = [k for k in tabs if k.startswith("snapshot__")]
    assert len(snaps) >= 10, f"rodada publicada sem copia congelada do cadastro: {snaps}"
    assert tabs["run_meta"].iloc[0]["banco_md5"], "sem md5 nao da para auditar a origem"
    # a proveniencia continua sendo o rotulo, nao o arquivo temporario
    assert tabs["run_meta"].iloc[0]["banco_arquivo"].startswith("postgres://")


def test_rodar_apaga_o_snapshot_temporario(job_dublado):
    import os
    espiao, vistos = job_dublado
    J.rodar("run_teste", "postgresql://dublê", max_time_s=20)
    assert not os.path.exists(vistos["snapshot_para"]), "o xlsx temporario ficou orfao"


def test_rodar_repassa_blob_e_criar_schema_false(job_dublado):
    espiao, _ = job_dublado
    J.rodar("run_teste", "postgresql://dublê", blob="abfss://x/y/", max_time_s=20)
    _, kw = espiao.publicado
    assert kw["blob"] == "abfss://x/y/"
    assert kw["criar_schema"] is False, "DDL nao pode rodar no caminho quente"
    assert kw["status_controle"] == ("run_teste", "controle")
