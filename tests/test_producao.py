"""Camada de PRODUCAO (job / portao de qualidade) — as regras que a revisao corrigiu e
que nao podem voltar a quebrar. Nao toca no motor nem nos valores golden.

Nenhum destes testes precisa de Postgres: o que se testa aqui e traducao de parametros e
logica do portao, ambos Python puro.
"""
import inspect

import pandas as pd
import pytest

from _helpers import engine

from otimizador.aplicacao import job_databricks as J
from otimizador.dominio import qualidade as Q


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
    from otimizador.infraestrutura import publicacao as PUB

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
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.infraestrutura import persistencia as P
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
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.infraestrutura import persistencia as P
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


def test_blob_uri_aponta_para_um_caminho_que_existe(tmp_path):
    """`otim_meta.blob_uri` e o ponteiro da AUDITORIA: e por ele que alguem acha, meses
    depois, a copia congelada do cadastro. Ate 2026-08-04 ele gravava
    `<destino>/run_id=<rid>` — um caminho que a gravacao nunca cria, porque `salvar`
    particiona por run_id DENTRO de cada tabela (`<destino>/<tabela>/run_id=<rid>/`).
    Nao havia perda de dado, mas quem seguisse o ponteiro nao achava nada."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    import glob
    import os
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.infraestrutura import persistencia as P
    from otimizador.infraestrutura import publicacao as PUB
    from _helpers import BANK_CTS, load_cts, build_all, silent
    M = engine()
    D.set_engine(M); P.set_engine(M, D)

    rid = "blob_ptr_1"
    tabs = silent(P.materializar, load_cts(True), build_all(load_cts(True)),
                  run_id=rid, banco="postgres://input", arquivo_fonte=BANK_CTS)
    silent(PUB.publicar_blob, tabs, str(tmp_path), verbose=False)

    uri = PUB.uri_blob(str(tmp_path), rid)
    assert os.path.isdir(uri), f"blob_uri aponta para caminho inexistente: {uri}"
    # e a copia congelada do cadastro daquela rodada e alcancavel a partir dele
    assert glob.glob(os.path.join(uri, "snapshot__*", f"run_id={rid}")), \
        "o snapshot da rodada nao e alcancavel a partir do blob_uri"


# ------------------------------------------- tabela obrigatoria mas vazia
def test_tabela_obrigatoria_vazia_e_erro(monkeypatch, tmp_path):
    """Tabela VAZIA nao e o mesmo que ausente. Um metas_cobertura existente porem vazio
    (carga interrompida, TRUNCATE indevido) produziria um Cenario sem metas, que resolve,
    passa no portao e publica SUCESSO."""
    import pandas as pd
    from otimizador.infraestrutura import carregar_postgres as C

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
    from otimizador.infraestrutura import carregar_postgres as C

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
    # rodar() chama o solver de verdade (CP.resolver_por_sistema): sem OR-Tools estes 4
    # testes FALHARIAM em vez de pular — quebrando o invariante "nenhum skip em vermelho".
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

    import sys as _sys
    import otimizador.infraestrutura as _infra
    monkeypatch.setattr(_infra, "publicacao", espiao)
    monkeypatch.setitem(_sys.modules, "otimizador.infraestrutura.publicacao", espiao)
    monkeypatch.setattr("otimizador.infraestrutura.carregar_postgres.carregar_postgres",
                        carregar_falso)
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


def test_rodar_falhou_qualidade_nao_publica(job_dublado, monkeypatch):
    """O ramo que o portao existe para acionar: grava o diagnostico, marca
    FALHOU_QUALIDADE e NAO publica. Ate agora so o caminho de sucesso era testado — e e
    justamente aqui que status, diagnostico e limpeza se combinam."""
    espiao, vistos = job_dublado
    monkeypatch.setattr(Q, "checar",
                        lambda cen, res, tabs: (False, [{"check": "x", "nivel": "critico",
                                                         "ok": False, "detalhe": "d"}],
                                                "QUALIDADE FALHOU — 1 de 1"))
    r = J.rodar("run_teste", "postgresql://dublê", max_time_s=20)

    assert r["status"] == "FALHOU_QUALIDADE"
    assert espiao.publicado is None, "reprovou no portao e mesmo assim publicou"
    assert espiao.status == ["RODANDO", "FALHOU_QUALIDADE"]
    assert espiao.diagnostico, "o relatorio tem de ficar gravado para o operador ler"
    import os
    assert not os.path.exists(vistos["snapshot_para"]), "o temporario vazou neste ramo"


def test_rodar_erro_marca_status_e_relevanta(job_dublado, monkeypatch):
    """Falha tecnica depois da carga: marca ERRO, re-levanta (para o run aparecer como
    falho no Databricks) e ainda assim limpa o temporario."""
    espiao, vistos = job_dublado

    def explode(*a, **k):
        raise RuntimeError("falha simulada no portao")

    monkeypatch.setattr(Q, "checar", explode)
    with pytest.raises(RuntimeError, match="falha simulada"):
        J.rodar("run_teste", "postgresql://dublê", max_time_s=20)

    assert espiao.publicado is None
    assert espiao.status == ["RODANDO", "ERRO"]
    import os
    assert not os.path.exists(vistos["snapshot_para"]), "o temporario vazou no ramo de erro"


def test_rodar_usa_nome_unico_para_o_snapshot(job_dublado):
    """Duas execucoes do mesmo run_id nao podem disputar o mesmo arquivo temporario."""
    espiao, vistos = job_dublado
    J.rodar("run_teste", "postgresql://dublê", max_time_s=20)
    primeiro = vistos["snapshot_para"]
    J.rodar("run_teste", "postgresql://dublê", max_time_s=20)
    assert vistos["snapshot_para"] != primeiro, "nome do snapshot nao e unico por execucao"
    assert "run_teste" in vistos["snapshot_para"], "o run_id some do nome e atrapalha o diagnostico"


# ------------------------------------------- idempotencia da copia em blob
def test_regravar_a_mesma_rodada_substitui_a_particao(tmp_path):
    """Reexecutar um `run_id` nao pode DUPLICAR a copia congelada.

    O `DELETE ... WHERE run_id` da publicacao no Postgres tornava a rodada
    idempotente daquele lado, e a documentacao dizia "tudo e idempotente" — mas o
    blob nao era: a gravacao via Spark era `mode("append")` particionada por
    `run_id`, entao o retry do job acrescentava arquivos DENTRO da particao em vez
    de troca-la. Como o blob e escrito ANTES da transacao do Postgres, ate um retry
    de rodada que falhou depois do blob ja duplicava. Quem seguisse o `blob_uri`
    meses depois encontraria o parquet com as linhas em dobro.
    """
    from otimizador.infraestrutura import persistencia as P

    tabs = {"run_ano": pd.DataFrame({"run_id": ["r1"] * 3, "ano": [2026, 2027, 2028]})}
    P.salvar(tabs, str(tmp_path), verbose=False)
    P.salvar(tabs, str(tmp_path), verbose=False)

    lido = P.carregar(str(tmp_path))["run_ano"]
    assert len(lido) == 3, f"a rodada foi duplicada: {len(lido)} linhas para 3 gravadas"


def test_regravar_nao_deixa_o_formato_antigo_convivendo(tmp_path):
    """`carregar()` le TUDO que estiver na pasta da particao. Se uma execucao caiu no
    fallback csv e a seguinte gravou parquet, sobrescrever `dados.parquet` nao basta —
    os dois arquivos coexistiriam e a rodada seria lida duas vezes."""
    import os
    from otimizador.infraestrutura import persistencia as P

    tabs = {"run_ano": pd.DataFrame({"run_id": ["r1"] * 3, "ano": [2026, 2027, 2028]})}
    P.salvar(tabs, str(tmp_path), formato="csv", verbose=False)
    P.salvar(tabs, str(tmp_path), formato="parquet", verbose=False)

    # Um arquivo, e nao "o arquivo tal": sem engine de parquet instalada o `salvar`
    # cai no fallback csv, e o que precisa valer nos dois casos e a AUSENCIA de
    # convivencia — dois arquivos na particao seriam duas leituras da mesma rodada.
    pasta = os.path.join(str(tmp_path), "run_ano", "run_id=r1")
    assert len(os.listdir(pasta)) == 1, os.listdir(pasta)
    assert len(P.carregar(str(tmp_path))["run_ano"]) == 3


def test_outras_rodadas_ficam_intactas(tmp_path):
    """Substituir a particao de uma rodada nao pode encostar nas demais — e a diferenca
    entre 'idempotente' e 'destrutivo'."""
    from otimizador.infraestrutura import persistencia as P

    P.salvar({"run_ano": pd.DataFrame({"run_id": ["r1"] * 2, "ano": [2026, 2027]})},
             str(tmp_path), verbose=False)
    P.salvar({"run_ano": pd.DataFrame({"run_id": ["r2"] * 2, "ano": [2026, 2027]})},
             str(tmp_path), verbose=False)
    P.salvar({"run_ano": pd.DataFrame({"run_id": ["r1"] * 2, "ano": [2026, 2027]})},
             str(tmp_path), verbose=False)

    lido = P.carregar(str(tmp_path))["run_ano"]
    assert sorted(lido.run_id.tolist()) == ["r1", "r1", "r2", "r2"], lido.run_id.tolist()


def test_a_particao_sai_do_dado_e_nao_do_run_meta(tmp_path):
    """A pasta e escolhida pelo `run_id` do PROPRIO df. Sem `run_meta` no conjunto, a
    versao anterior inventava um id novo (`novo_run_id()`) e gravava numa particao que
    nao correspondia ao dado — cada regravacao criava outra pasta, e `carregar()` somava
    todas elas."""
    from otimizador.infraestrutura import persistencia as P

    tabs = {"run_ano": pd.DataFrame({"run_id": ["r9"] * 2, "ano": [2026, 2027]})}
    P.salvar(tabs, str(tmp_path), verbose=False)   # sem run_meta de proposito
    P.salvar(tabs, str(tmp_path), verbose=False)

    import os
    assert os.listdir(os.path.join(str(tmp_path), "run_ano")) == ["run_id=r9"]
    assert len(P.carregar(str(tmp_path))["run_ano"]) == 2


# ------------------------------------------- idempotencia no ramo Spark (producao)
class _EscritorDuble:
    """Registra a chamada de escrita em vez de gravar. E o que interessa testar:
    QUAL escrita o `salvar` escolhe — o dado em si ja e coberto pelo ramo pandas."""

    def __init__(self, reg):
        self.reg = reg

    def format(self, f):
        self.reg["format"] = f
        return self

    def mode(self, m):
        self.reg["mode"] = m
        return self

    def option(self, k, v):
        self.reg["options"][k] = v
        return self

    def partitionBy(self, *cols):
        self.reg["partitionBy"] = list(cols)
        return self

    def save(self, caminho):
        self.reg["save"] = caminho

    def saveAsTable(self, alvo):
        self.reg["saveAsTable"] = alvo


def _spark_duble(existentes=(), tabelas=()):
    """SparkSession de mentira: escrita registrada e um FileSystem Hadoop de mentira,
    para observar o que e apagado antes de gravar."""
    import types

    estado = {"escritas": [], "apagados": []}

    class _FS:
        def exists(self, p):
            return str(p) in existentes

        def delete(self, p, recursivo):
            estado["apagados"].append(str(p))
            return True

    class _Path:
        def __init__(self, s):
            self.s = s

        def __str__(self):
            return self.s

        def getFileSystem(self, conf):
            return _FS()

    def cria_df(df):
        reg = {"options": {}}
        estado["escritas"].append(reg)
        return types.SimpleNamespace(write=_EscritorDuble(reg))

    ns = types.SimpleNamespace
    sp = ns(
        createDataFrame=cria_df,
        catalog=ns(tableExists=lambda alvo: alvo in tabelas),
        _jvm=ns(org=ns(apache=ns(hadoop=ns(fs=ns(Path=_Path))))),
        _jsc=ns(hadoopConfiguration=lambda: None),
    )
    return sp, estado


def test_spark_apaga_a_particao_da_rodada_antes_de_acrescentar(monkeypatch):
    """O caminho de PRODUCAO: `publicar_blob` -> `salvar(formato="parquet")` com destino
    `abfss://`. Ate 2026-08-06 era `mode("append")` particionado por `run_id` — sem
    apagar nada antes, o que duplica o parquet a cada reexecucao do mesmo `run_id`."""
    from otimizador.infraestrutura import persistencia as P

    raiz = "abfss://c@x.dfs.core.windows.net/otim"
    particao = f"{raiz}/run_ano/run_id=r1"
    sp, estado = _spark_duble(existentes=(particao,))  # a rodada JA foi gravada antes
    monkeypatch.setattr(P, "_spark", lambda: sp)
    tabs = {"run_ano": pd.DataFrame({"run_id": ["r1"] * 3, "ano": [2026, 2027, 2028]})}
    P.salvar(tabs, raiz, verbose=False)

    assert estado["apagados"] == [particao]
    (esc,) = estado["escritas"]
    assert esc["partitionBy"] == ["run_id"]
    # `append` DEPOIS de apagar, e nao `overwrite`: overwrite sem particao dinamica
    # levaria a pasta inteira, com as outras rodadas dentro.
    assert esc["mode"] == "append"


def test_spark_sem_particao_nao_apaga_nada(monkeypatch):
    """`particionar_por_run=False` nao tem particao a substituir. O risco aqui seria o
    inverso do bug: apagar `<base>` inteiro, levando junto todas as rodadas."""
    from otimizador.infraestrutura import persistencia as P

    sp, estado = _spark_duble()
    monkeypatch.setattr(P, "_spark", lambda: sp)
    P.salvar({"run_ano": pd.DataFrame({"run_id": ["r1"], "ano": [2026]})},
             "abfss://c@x/otim", particionar_por_run=False, verbose=False)

    assert estado["apagados"] == []
    (esc,) = estado["escritas"]
    assert esc["mode"] == "append" and "partitionBy" not in esc


def test_delta_substitui_pelo_log_e_nunca_apagando_pasta(monkeypatch):
    """Num Delta, apagar `run_id=<rid>/` corromperia o log (os arquivos continuariam
    referenciados). Quem substitui a rodada tem de ser o proprio Delta."""
    from otimizador.infraestrutura import persistencia as P

    base = "abfss://c@x/otim/run_ano"
    sp, estado = _spark_duble(existentes=(f"{base}/_delta_log",))
    monkeypatch.setattr(P, "_spark", lambda: sp)
    P.salvar({"run_ano": pd.DataFrame({"run_id": ["r1"] * 2, "ano": [2026, 2027]})},
             "abfss://c@x/otim", formato="delta", verbose=False)

    assert estado["apagados"] == [], "nao se apaga pasta de Delta"
    (esc,) = estado["escritas"]
    assert esc["mode"] == "overwrite"
    assert esc["options"]["replaceWhere"] == "run_id = 'r1'"
    assert esc["partitionBy"] == ["run_id"]


def test_delta_novo_e_criado_com_append(monkeypatch):
    """`replaceWhere` sobre tabela que ainda nao existe falha. Na primeira gravacao nao
    ha o que substituir."""
    from otimizador.infraestrutura import persistencia as P

    sp, estado = _spark_duble()  # sem _delta_log
    monkeypatch.setattr(P, "_spark", lambda: sp)
    P.salvar({"run_ano": pd.DataFrame({"run_id": ["r1"], "ano": [2026]})},
             "abfss://c@x/otim", formato="delta", verbose=False)

    (esc,) = estado["escritas"]
    assert esc["mode"] == "append" and "replaceWhere" not in esc["options"]


def test_salvar_delta_substitui_a_rodada_por_padrao(monkeypatch):
    """`salvar_delta` tinha `modo="append"` no default — mesmo defeito, na API que a
    documentacao recomenda para o Databricks."""
    from otimizador.infraestrutura import persistencia as P

    sp, estado = _spark_duble(tabelas=("cat.otim.run_ano",))
    monkeypatch.setattr(P, "_spark", lambda: sp)
    P.salvar_delta({"run_ano": pd.DataFrame({"run_id": ["r1"] * 2, "ano": [2026, 2027]})},
                   "cat.otim", verbose=False)

    (esc,) = estado["escritas"]
    assert esc["mode"] == "overwrite"
    assert esc["options"]["replaceWhere"] == "run_id = 'r1'"
    assert esc["saveAsTable"] == "cat.otim.run_ano"


def test_salvar_delta_aceita_modo_explicito(monkeypatch):
    """Quem quiser append cru continua podendo — so nao ganha isso sem pedir."""
    from otimizador.infraestrutura import persistencia as P

    sp, estado = _spark_duble(tabelas=("cat.otim.run_ano",))
    monkeypatch.setattr(P, "_spark", lambda: sp)
    P.salvar_delta({"run_ano": pd.DataFrame({"run_id": ["r1"], "ano": [2026]})},
                   "cat.otim", modo="append", verbose=False)

    (esc,) = estado["escritas"]
    assert esc["mode"] == "append" and "replaceWhere" not in esc["options"]


# ------------------------------------------- o run_id vira caminho e literal SQL
@pytest.mark.parametrize("rid", [
    "r1' OR run_id <> 'r1",   # fecha o literal do replaceWhere: casaria com TUDO
    "a/../../outra",          # sai da particao no caminho apagado
    "com espaco",             # o Spark ESCAPA ao gravar: a pasta real tem outro nome
    "tem=igual",              # idem — e ainda confunde o parser de particao
    "",                       # particao `run_id=` sem valor
])
def test_run_id_hostil_e_recusado(rid, tmp_path):
    """O `run_id` e escolhido pelo BACKEND (`docs/02`) e a coluna e `text` sem gramatica
    no DDL — nao ha barreira antes daqui. Ele e usado em dois lugares perigosos:

    1. literal do `replaceWhere` do Delta. `r1' OR run_id <> 'r1` vira
       `run_id = 'r1' OR run_id <> 'r1'`, condicao que casa com TUDO: o `overwrite`
       levaria a tabela inteira, com todas as rodadas dentro. Seria pior que o bug
       original, que so duplicava.
    2. caminho da particao apagada. `/` e `..` desviam o `delete` para fora dela.

    E ha um terceiro, mais sorrateiro: caracteres que o Spark escapa ao gravar particao
    (espaco, `=`, `%`, `/`) fazem a pasta real ter outro nome, que o caminho montado
    aqui nao encontra — o delete vira no-op e a duplicacao volta em silencio.
    """
    from otimizador.infraestrutura import persistencia as P

    tabs = {"run_ano": pd.DataFrame({"run_id": [rid], "ano": [2026]})}
    with pytest.raises(ValueError, match="run_id invalido"):
        P.salvar(tabs, str(tmp_path), verbose=False)


@pytest.mark.parametrize("rid", ["r1", "run_20260806_120000_ab12cd", "run-2026.08", "R1"])
def test_run_id_normal_continua_passando(rid, tmp_path):
    """A guarda nao pode recusar o que o proprio pacote gera: `novo_run_id()` produz
    `run_<data>_<hex>`, e os rotulos usados a mao sao alfanumericos com `_-.`."""
    from otimizador.infraestrutura import persistencia as P

    P.salvar({"run_ano": pd.DataFrame({"run_id": [rid], "ano": [2026]})},
             str(tmp_path), verbose=False)
    assert len(P.carregar(str(tmp_path))["run_ano"]) == 1


def test_novo_run_id_passa_na_propria_guarda():
    """Trava a coerencia entre gerador e validador: se um mudar sem o outro, o pacote
    passa a recusar o id que ele mesmo gera."""
    from otimizador.infraestrutura import persistencia as P

    assert P._exigir_run_id_seguro(P.novo_run_id())


def test_dois_run_id_no_mesmo_df_e_erro(tmp_path):
    """"Substituir a rodada" nao tem significado com duas rodadas no mesmo conjunto: no
    Spark apagaria duas particoes numa chamada; no pandas colocaria as linhas de uma
    dentro da pasta da outra. O portao de qualidade barra isso antes de publicar, mas
    `salvar` e API publica e precisa da propria guarda."""
    from otimizador.infraestrutura import persistencia as P

    tabs = {"run_ano": pd.DataFrame({"run_id": ["r1", "r2"], "ano": [2026, 2027]})}
    with pytest.raises(ValueError, match="exatamente um run_id"):
        P.salvar(tabs, str(tmp_path), verbose=False)


def test_run_id_todo_nulo_e_erro(tmp_path):
    """Antes caia num fallback: no `salvar_delta` a condicao virava `run_id = ''`, que
    nao casa com linha nenhuma — append disfarcado de substituicao."""
    from otimizador.infraestrutura import persistencia as P

    tabs = {"run_ano": pd.DataFrame({"run_id": [None, None], "ano": [2026, 2027]})}
    with pytest.raises(ValueError, match="exatamente um run_id"):
        P.salvar(tabs, str(tmp_path), verbose=False)


def test_falha_ao_consultar_o_catalogo_propaga(monkeypatch):
    """`_tabela_existe` engolia QUALQUER excecao e respondia "nao existe", o que escolhe
    o `append`. Permissao negada ou metastore fora do ar viraria append sobre tabela que
    existe — a duplicacao de volta, em silencio, e justo no ambiente onde ninguem olha.
    Agora so a ausencia da API (Spark antigo) vira False."""
    import types
    from otimizador.infraestrutura import persistencia as P

    def explode(alvo):
        raise PermissionError("metastore indisponivel")

    sp, _ = _spark_duble()
    sp.catalog = types.SimpleNamespace(tableExists=explode)
    monkeypatch.setattr(P, "_spark", lambda: sp)
    with pytest.raises(PermissionError):
        P.salvar_delta({"run_ano": pd.DataFrame({"run_id": ["r1"], "ano": [2026]})},
                       "cat.otim", verbose=False)


def test_spark_antigo_sem_a_api_cai_para_criacao(monkeypatch):
    """O unico fallback que sobra: Spark sem `catalog.tableExists`."""
    import types
    from otimizador.infraestrutura import persistencia as P

    sp, estado = _spark_duble()
    sp.catalog = types.SimpleNamespace()  # sem tableExists -> AttributeError
    monkeypatch.setattr(P, "_spark", lambda: sp)
    P.salvar_delta({"run_ano": pd.DataFrame({"run_id": ["r1"], "ano": [2026]})},
                   "cat.otim", verbose=False)

    (esc,) = estado["escritas"]
    assert esc["mode"] == "append" and "replaceWhere" not in esc["options"]
