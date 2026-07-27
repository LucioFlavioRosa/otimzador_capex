"""Publicacao contra um Postgres DE VERDADE — idempotencia, transacao e cascade.

E a unica parte do pacote que nao da para provar offline: `publicar_postgres` so e
atomica se o commit/rollback do psycopg2 se comportar como esperado, e a republicacao do
mesmo `run_id` so e limpa se o `DELETE ... WHERE run_id` + `ON DELETE CASCADE` existirem
de fato no banco.

    # Postgres efemero (descarta ao parar):
    docker run --rm -d -p 5433:5432 -e POSTGRES_PASSWORD=teste --name pg-otim postgres:16

    # Windows PowerShell
    $env:OTIMIZADOR_PG_TESTE = "postgresql://postgres:teste@localhost:5433/postgres"
    # bash
    export OTIMIZADOR_PG_TESTE="postgresql://postgres:teste@localhost:5433/postgres"

    pytest tests/test_publicacao_postgres.py -v

Sem a variavel de ambiente o modulo inteiro e PULADO — o CI offline continua verde.
O teste cria dois schemas proprios (`otim_teste_pub` / `otim_teste_ctrl`) e os derruba no
fim: nao encosta em `public`, `input` nem `controle`.
"""
import os
import re

import pytest

PG = os.environ.get("OTIMIZADOR_PG_TESTE")
pytestmark = pytest.mark.skipif(
    not PG, reason="defina OTIMIZADOR_PG_TESTE com a URL de um Postgres de teste")

SCHEMA_PUB = "otim_teste_pub"
SCHEMA_CTRL = "otim_teste_ctrl"


# --------------------------------------------------------------------- infra
def _conn():
    psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2-binary nao instalado")
    return psycopg2.connect(PG)


def _ddl_controle(schema):
    """Reaproveita o trecho CONTROLE do ddl_input.sql entregue, so trocando o schema.

    De proposito NAO duplicamos o DDL aqui: se o `ddl_input.sql` mudar (uma coluna nova,
    um CHECK novo), este teste passa a exercitar a versao nova sem ninguem lembrar de
    atualizar o teste.
    """
    from _helpers import ROOT
    with open(os.path.join(ROOT, "ddl_input.sql"), encoding="utf-8") as f:
        sql = f.read()
    i = sql.index("-- ---- CONTROLE")
    corpo = sql[i:].replace("controle.", f"{schema}.")
    # os nomes de indice/constraint sao globais por schema; prefixa para nao colidir
    corpo = re.sub(r"\b(ix_diag_run)\b", r"\1_teste", corpo)
    return f"CREATE SCHEMA IF NOT EXISTS {schema};\n" + corpo


@pytest.fixture(scope="module")
def tabs_base():
    """Uma materializacao real (build-all sobre o banco de teste CTS — sem solver, rapida)."""
    pytest.importorskip("matplotlib", reason="dashboard_otimizador_v2 exige matplotlib")
    import dashboard_otimizador_v2 as D
    import persistencia as P
    from _helpers import engine, load_cts, build_all, silent
    M = engine()
    D.set_engine(M); P.set_engine(M, D)
    cen = load_cts(True)
    return silent(P.materializar, cen, build_all(cen), run_id="rodada_A", banco="pg-teste")


@pytest.fixture(scope="module", autouse=True)
def esquemas(tabs_base):
    """Cria os dois schemas do teste (DDL de resultado + DDL de controle) e derruba no fim."""
    import publicacao as PUB
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA_PUB} CASCADE;")
                cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA_CTRL} CASCADE;")
                # a DDL de resultado sai do proprio gerador que o DBA usaria
                cur.execute(PUB.ddl_postgres(tabs_base, schema=SCHEMA_PUB))
                cur.execute(_ddl_controle(SCHEMA_CTRL))
        yield
    finally:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA_PUB} CASCADE;")
                cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA_CTRL} CASCADE;")
        conn.close()


@pytest.fixture(autouse=True)
def banco_limpo():
    """Cada teste comeca sem nenhuma rodada gravada."""
    conn = _conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {SCHEMA_PUB}.otim_meta;")          # CASCADE nos detalhes
            cur.execute(f"DELETE FROM {SCHEMA_CTRL}.run_diagnostico;")
            cur.execute(f"DELETE FROM {SCHEMA_CTRL}.run_status;")
            cur.execute(f"DELETE FROM {SCHEMA_CTRL}.run_request;")
    conn.close()
    yield


# ------------------------------------------------------------------ helpers
def com_run_id(tabs, rid):
    """Copia das tabelas com outro run_id (nao muta o fixture de modulo)."""
    return {k: (df.assign(run_id=rid) if "run_id" in getattr(df, "columns", []) else df)
            for k, df in tabs.items()}


def contar(tabela, run_id=None, schema=SCHEMA_PUB):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            if run_id is None:
                cur.execute(f"SELECT count(*) FROM {schema}.{tabela};")
            else:
                cur.execute(f"SELECT count(*) FROM {schema}.{tabela} WHERE run_id = %s;", (run_id,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def criar_run_request(run_id):
    """run_status tem FK para run_request — a rodada precisa existir antes."""
    conn = _conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {SCHEMA_CTRL}.run_request (run_id, params) "
                        f"VALUES (%s, %s::jsonb) ON CONFLICT (run_id) DO NOTHING;",
                        (run_id, '{"UNIDADE": "u1", "ORCAMENTO": 1000000}'))
    conn.close()


TABELAS_DETALHE = ["otim_obra", "otim_subbacia", "otim_ano", "otim_cidade_ano"]


# ------------------------------------------------------------- IDEMPOTENCIA
def test_republicar_o_mesmo_run_id_nao_duplica(tabs_base):
    """O contrato do README: 'reprocessar o mesmo run_id e seguro'. Era falso enquanto o
    job nao passava run_id= para materializar (cada retry publicava um conjunto NOVO)."""
    import publicacao as PUB
    PUB.publicar_postgres(tabs_base, PG, schema=SCHEMA_PUB, criar=False, verbose=False)
    antes = {t: contar(t, "rodada_A") for t in TABELAS_DETALHE}
    assert contar("otim_meta", "rodada_A") == 1
    assert all(v > 0 for v in antes.values()), antes

    PUB.publicar_postgres(tabs_base, PG, schema=SCHEMA_PUB, criar=False, verbose=False)

    assert contar("otim_meta", "rodada_A") == 1, "a rodada foi duplicada no cabecalho"
    assert {t: contar(t, "rodada_A") for t in TABELAS_DETALHE} == antes, \
        "os detalhes foram duplicados na republicacao"
    assert contar("otim_meta") == 1, "sobrou cabecalho de outra rodada"


def test_republicar_substitui_os_detalhes_da_rodada_anterior(tabs_base):
    """Republicar com menos obras tem de APAGAR as antigas (DELETE + ON DELETE CASCADE),
    nao apenas somar. Se a FK cascade nao existir no banco, este teste falha."""
    import publicacao as PUB
    PUB.publicar_postgres(tabs_base, PG, schema=SCHEMA_PUB, criar=False, verbose=False)
    n_completo = contar("otim_obra", "rodada_A")
    assert n_completo >= 2

    reduzido = dict(tabs_base)
    reduzido["run_obra"] = tabs_base["run_obra"].head(1)
    PUB.publicar_postgres(reduzido, PG, schema=SCHEMA_PUB, criar=False, verbose=False)

    assert contar("otim_obra", "rodada_A") == 1, \
        "as obras da publicacao anterior nao foram apagadas"


def test_republicar_nao_afeta_as_outras_rodadas(tabs_base):
    """O DELETE e por run_id: o historico das outras rodadas nao pode se mexer."""
    import publicacao as PUB
    a, b = tabs_base, com_run_id(tabs_base, "rodada_B")
    PUB.publicar_postgres(a, PG, schema=SCHEMA_PUB, criar=False, verbose=False)
    PUB.publicar_postgres(b, PG, schema=SCHEMA_PUB, criar=False, verbose=False)
    n_b = contar("otim_obra", "rodada_B")

    PUB.publicar_postgres(a, PG, schema=SCHEMA_PUB, criar=False, verbose=False)

    assert contar("otim_meta") == 2
    assert contar("otim_obra", "rodada_B") == n_b


# ---------------------------------------------------------------- TRANSACAO
def test_falha_no_meio_da_publicacao_nao_grava_nada(tabs_base):
    """`run_meta` e `run_obra` entram antes de `run_ano`. Com uma coluna inexistente em
    run_ano, o INSERT estoura no meio — e o que ja entrou tem de sumir no rollback."""
    import publicacao as PUB
    quebrado = dict(tabs_base)
    quebrado["run_ano"] = tabs_base["run_ano"].assign(coluna_que_nao_existe=1)

    with pytest.raises(Exception):
        PUB.publicar_postgres(quebrado, PG, schema=SCHEMA_PUB, criar=False, verbose=False)

    assert contar("otim_meta") == 0, "a publicacao nao foi atomica: sobrou cabecalho"
    assert contar("otim_obra") == 0, "a publicacao nao foi atomica: sobraram detalhes"


def test_publicacao_e_status_entram_no_mesmo_commit(tabs_base):
    """A propriedade que o job depende (D5b): com a conexao vinda de fora, `publicar_postgres`
    NAO commita nem fecha — quem manda e o chamador. Se algo falhar depois de publicar, o
    SUCESSO e os dados voltam JUNTOS, e o estado observavel nunca mente."""
    import publicacao as PUB
    criar_run_request("rodada_A")
    conn = _conn()
    try:
        with pytest.raises(RuntimeError):
            with conn:
                PUB.publicar_postgres(tabs_base, conn, schema=SCHEMA_PUB,
                                      criar=False, verbose=False)
                PUB.marcar_status_controle(conn, "rodada_A", "SUCESSO", schema=SCHEMA_CTRL)
                raise RuntimeError("falha simulada DEPOIS de publicar e marcar")
    finally:
        conn.close()

    assert contar("otim_meta") == 0, "os dados foram commitados apesar da falha"
    assert contar("run_status", schema=SCHEMA_CTRL) == 0, "o SUCESSO ficou gravado sozinho"


def test_publicar_com_status_controle_commita_os_dois_juntos(tabs_base):
    """O caminho que o job usa: `publicar(status_controle=(run_id, schema))` grava as run_*
    e o SUCESSO na mesma transacao, sem o job precisar abrir conexao na mao."""
    import publicacao as PUB
    criar_run_request("rodada_A")
    PUB.publicar(tabs_base, pg=PG, schema=SCHEMA_PUB, criar_schema=False,
                 status_controle=("rodada_A", SCHEMA_CTRL), verbose=False)

    assert contar("otim_meta", "rodada_A") == 1
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT status FROM {SCHEMA_CTRL}.run_status WHERE run_id = %s;",
                        ("rodada_A",))
            assert cur.fetchone()[0] == "SUCESSO"
    finally:
        conn.close()


def test_publicar_com_status_controle_desfaz_os_dois_juntos(tabs_base):
    """Se a publicacao falhar, o SUCESSO nao pode ficar gravado sozinho."""
    import publicacao as PUB
    criar_run_request("rodada_A")
    quebrado = dict(tabs_base)
    quebrado["run_ano"] = tabs_base["run_ano"].assign(coluna_que_nao_existe=1)

    with pytest.raises(Exception):
        PUB.publicar(quebrado, pg=PG, schema=SCHEMA_PUB, criar_schema=False,
                     status_controle=("rodada_A", SCHEMA_CTRL), verbose=False)

    assert contar("otim_meta") == 0
    assert contar("run_status", schema=SCHEMA_CTRL) == 0


def test_conexao_de_fora_nao_e_fechada(tabs_base):
    """publicar_postgres fechava a conexao do chamador no finally — o que impedia compor
    publicacao e status na mesma transacao."""
    import publicacao as PUB
    conn = _conn()
    try:
        with conn:
            PUB.publicar_postgres(tabs_base, conn, schema=SCHEMA_PUB,
                                  criar=False, verbose=False)
        assert not conn.closed, "a conexao do chamador foi fechada"
        with conn.cursor() as cur:                       # ainda utilizavel
            cur.execute("SELECT 1;")
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


# ----------------------------------------------------------------- CONTROLE
def test_marcar_status_controle_faz_upsert():
    import publicacao as PUB
    criar_run_request("rodada_A")
    for st in ("RODANDO", "SUCESSO"):
        PUB.marcar_status_controle(PG, "rodada_A", st, schema=SCHEMA_CTRL)

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT status, count(*) OVER () FROM {SCHEMA_CTRL}.run_status "
                        f"WHERE run_id = %s;", ("rodada_A",))
            status, n = cur.fetchone()
    finally:
        conn.close()
    assert (status, n) == ("SUCESSO", 1), "upsert virou insert duplicado"


def test_status_fora_do_dominio_e_rejeitado():
    """CHECK do ddl_input.sql: um status escrito errado nao pode entrar em silencio."""
    import publicacao as PUB
    criar_run_request("rodada_A")
    with pytest.raises(Exception):
        PUB.marcar_status_controle(PG, "rodada_A", "CONCLUIDO", schema=SCHEMA_CTRL)
    assert contar("run_status", schema=SCHEMA_CTRL) == 0


def test_gravar_diagnostico_e_idempotente():
    """Reprocessar a rodada nao pode acumular relatorios de qualidade."""
    import publicacao as PUB
    rel = [{"check": "Status do solver", "nivel": "critico", "ok": True, "detalhe": "OTIMO"},
           {"check": "Plano nao-vazio", "nivel": "aviso", "ok": True, "detalhe": "28 obras"}]
    for _ in range(3):
        PUB.gravar_diagnostico(PG, "rodada_A", rel, schema=SCHEMA_CTRL)

    assert contar("run_diagnostico", "rodada_A", schema=SCHEMA_CTRL) == len(rel)


def test_diagnostico_de_outra_rodada_sobrevive():
    import publicacao as PUB
    rel = [{"check": "x", "nivel": "critico", "ok": False, "detalhe": "d"}]
    PUB.gravar_diagnostico(PG, "rodada_A", rel, schema=SCHEMA_CTRL)
    PUB.gravar_diagnostico(PG, "rodada_B", rel, schema=SCHEMA_CTRL)
    PUB.gravar_diagnostico(PG, "rodada_A", rel, schema=SCHEMA_CTRL)

    assert contar("run_diagnostico", "rodada_B", schema=SCHEMA_CTRL) == 1
