"""SMOKE TEST — o pipeline inteiro contra um Postgres de verdade, de ponta a ponta.

E o primeiro comando a rodar quando existir um banco. Ate ele passar, nada neste pacote
foi validado contra um Postgres: todo o caminho de escrita e leitura esta correto por
LEITURA DE CODIGO, nao por execucao.

    # Postgres efemero:
    docker run --rm -d -p 5433:5432 -e POSTGRES_PASSWORD=teste --name pg-otim postgres:16

    python main.py smoke --pg "postgresql://postgres:teste@localhost:5433/postgres"

O que ele faz, nesta ordem (cada passo imprime OK/FALHA e o porque):

    1. aplica ddl_input.sql        -> schemas de cadastro e controle
    2. aplica ddl_resultado.sql    -> 14 tabelas otim_* + 3 views
    3. carrega uma fixture nas tabelas de input  (exercita PKs e FKs com dado real)
    4. insere uma controle.run_request
    5. roda o job fim-a-fim       -> job_databricks.rodar(...)
    6. confere no BANCO as reconciliacoes do portao de qualidade
    7. RODA DE NOVO o mesmo run_id -> prova a idempotencia (o teste que so o banco da)
    8. confere que nada duplicou

Por padrao usa schemas proprios (`smoke_input`, `smoke_controle`, `smoke_public`) e os
derruba no fim: nao encosta em input/controle/public. Com `--schemas-reais` roda contra os
nomes de producao — mais fiel, e o que voce quer num banco descartavel.

Opcoes:
    --pg URL           conexao (ou variavel de ambiente OTIMIZADOR_PG_TESTE)
    --fixture ARQ      banco de teste a carregar (default: o banco com CTS)
    --schemas-reais    usa input/controle/public em vez dos schemas smoke_*
    --forcar           prossegue mesmo se os schemas ja tiverem dados (APAGA)
    --manter           nao derruba os schemas no fim (para inspecionar o resultado)
"""
from __future__ import annotations

import argparse
import io
import contextlib
import json
import os
import sys
import traceback

import matplotlib
matplotlib.use("Agg")                     # o dashboard importa pyplot; sem backend grafico

# este script vive em scripts/; a raiz do repo (onde esta o pacote `otimizador`) e o pai
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SQL = os.path.join("otimizador", "infraestrutura", "sql")

FIXTURE_PADRAO = os.path.join("tests", "fixtures", "banco_teste_CTS_poc_v2.json")
RUN_ID = "smoke_0001"

# ORDEM DE CARGA — nao e a ordem do ABAS_INPUT.
# `ABAS_INPUT` lista subbacia-cts ANTES de cts-operacional; para LER tanto faz, mas para
# GRAVAR a FK subbacia_cts.cts -> cts_operacional.cts exige o inverso. Toda tabela aqui vem
# depois daquela que ela referencia.
ORDEM_CARGA = [
    "unidade-regional",
    "regional-superintendencia",
    "superintendencia-cidade",
    "cidade-sistema",
    "sistema-topologia",
    "cidade-operacional",
    "subbacia-operacional",
    "componentes-subbacias-capex",
    "ete-capex",
    "regional-operacional",
    "metas-cobertura",
    "fator-esgoto",
    "cts-operacional",          # antes de subbacia-cts (FK)
    "subbacia-cts",
    "componentes-cts-capex",
    "orcamento",
]

# ORCAMENTO com chave STRING de proposito: e assim que o JSONB devolve, e exercita a
# conversao para int que o job faz (sem ela o teto vira INF e o CP-SAT estoura).
PARAMS_RODADA = {
    "ORCAMENTO": {"2026": 20_000_000, "2027": 20_000_000, "2028": 20_000_000},
    "BASE_RECEITA": "arrecadada",
    "USAR_CTS": True,
    "INCLUIR_INDUSTRIAL": True,
    "USUARIO": "smoke-test",
    "MAX_TIME_S": 60,
}


# --------------------------------------------------------------------- relatorio
class Relatorio:
    def __init__(self):
        self.linhas = []

    def ok(self, passo, detalhe=""):
        self.linhas.append((True, passo, detalhe))
        print(f"  [OK   ] {passo:<52}{detalhe}")

    def falha(self, passo, detalhe=""):
        self.linhas.append((False, passo, detalhe))
        print(f"  [FALHA] {passo:<52}{detalhe}")

    @property
    def tudo_bem(self):
        return all(ok for ok, _, _ in self.linhas)

    def resumo(self):
        n = len(self.linhas)
        maus = [p for ok, p, _ in self.linhas if not ok]
        print("-" * 78)
        if self.tudo_bem:
            print(f"  SMOKE TEST OK — {n} verificacoes passaram")
        else:
            print(f"  SMOKE TEST FALHOU — {len(maus)} de {n}:")
            for p in maus:
                print(f"      · {p}")
        print("=" * 78)


# --------------------------------------------------------------------- utilidades
def _conn(pg):
    import psycopg2
    return psycopg2.connect(pg)


def _um(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args or ())
        linha = cur.fetchone()
    return linha[0] if linha else None


def _sql_com_schemas(caminho, mapa):
    """Le um .sql e troca os nomes de schema. Exercita o MESMO arquivo que vai para
    producao — nada de DDL duplicada aqui dentro."""
    with open(os.path.join(ROOT, caminho), encoding="utf-8") as f:
        sql = f.read()
    for original, novo in mapa.items():
        if original == novo:
            continue
        sql = sql.replace(f"{original}.", f"{novo}.")
        sql = sql.replace(f"CREATE SCHEMA IF NOT EXISTS {original};",
                          f"CREATE SCHEMA IF NOT EXISTS {novo};")
    return sql


def _colunas(conn, schema, tabela):
    with conn.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = %s""", (schema, tabela))
        return {r[0] for r in cur.fetchall()}


def _norm(c):
    """Mesma normalizacao de cabecalho que o `ler_banco` faz."""
    return str(c).strip().lower().replace(" ", "_").replace("-", "_")


def _tem_dados(conn, schemas):
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*) FROM information_schema.tables
                        WHERE table_schema = ANY(%s)""", (list(schemas),))
        return cur.fetchone()[0] > 0


def _derrubar(conn, schemas):
    with conn:
        with conn.cursor() as cur:
            for s in schemas:
                cur.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE;")


# --------------------------------------------------------------------- os passos
def passo_ddl(conn, rel, s_in, s_ctrl, s_pub):
    for arquivo, mapa in ((os.path.join(SQL, "ddl_input.sql"), {"input": s_in, "controle": s_ctrl}),
                          (os.path.join(SQL, "ddl_resultado.sql"), {"public": s_pub})):
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_sql_com_schemas(arquivo, mapa))
            rel.ok(f"aplica {arquivo}")
        except Exception as e:
            rel.falha(f"aplica {arquivo}", f"{type(e).__name__}: {e}")
            return False

    n_in = _um(conn, """SELECT count(*) FROM information_schema.tables
                         WHERE table_schema = %s AND table_type = 'BASE TABLE'""", (s_in,))
    n_pub = _um(conn, """SELECT count(*) FROM information_schema.tables
                          WHERE table_schema = %s AND table_type = 'BASE TABLE'""", (s_pub,))
    n_vw = _um(conn, "SELECT count(*) FROM information_schema.views WHERE table_schema = %s",
               (s_pub,))
    rel.ok("tabelas criadas", f"{n_in} de entrada · {n_pub} de saida · {n_vw} views") \
        if (n_in, n_pub, n_vw) == (16, 14, 3) else \
        rel.falha("tabelas criadas", f"esperado 16/14/3, veio {n_in}/{n_pub}/{n_vw}")

    # o CASCADE e o que faz a republicacao ficar limpa — confirma que a FK existe mesmo
    n_fk = _um(conn, """SELECT count(*) FROM information_schema.referential_constraints rc
                          JOIN information_schema.table_constraints tc
                            ON tc.constraint_name = rc.constraint_name
                         WHERE tc.table_schema = %s AND rc.delete_rule = 'CASCADE'""", (s_pub,))
    rel.ok("FK com ON DELETE CASCADE", f"{n_fk} tabelas de detalhe") if n_fk >= 13 else \
        rel.falha("FK com ON DELETE CASCADE", f"esperado >= 13, veio {n_fk}")
    return True


def passo_carga(conn, rel, s_in, fixture):
    import pandas as pd
    from psycopg2.extras import execute_values
    from otimizador.infraestrutura import carregar_postgres as C

    caminho = fixture if os.path.isabs(fixture) else os.path.join(ROOT, fixture)
    if not os.path.exists(caminho):
        rel.falha("carrega o cadastro", f"fixture nao encontrada: {caminho}")
        return False

    with open(caminho, encoding="utf-8") as f:
        abas = json.load(f)
    total, descartadas = 0, []
    try:
        with conn:
            with conn.cursor() as cur:
                for aba in ORDEM_CARGA:
                    if aba not in abas or not abas[aba]:
                        continue
                    # `TABELAS_DE_CARGA`, e nao `ABAS_INPUT`: aquele mapa guarda a CONSULTA
                    # de leitura, que para a hierarquia v8 e uma projecao — escrever de
                    # volta precisa do nome da tabela e do de-para das colunas.
                    tabela, renomear = C.TABELAS_DE_CARGA[aba]
                    df = pd.DataFrame(abas[aba])
                    df.columns = [renomear.get(_norm(c), _norm(c)) for c in df.columns]
                    cols_db = _colunas(conn, s_in, tabela)
                    fora = [c for c in df.columns if c not in cols_db and not c.startswith("unnamed")]
                    if fora:
                        descartadas.append(f"{tabela}:{','.join(fora)}")
                    usaveis = [c for c in df.columns if c in cols_db]
                    if not usaveis or df.empty:
                        continue
                    d = df[usaveis].astype(object).where(pd.notna(df[usaveis]), None)
                    execute_values(
                        cur,
                        f'INSERT INTO {s_in}."{tabela}" ({", ".join(usaveis)}) VALUES %s',
                        [tuple(r) for r in d.itertuples(index=False, name=None)])
                    total += len(d)
    except Exception as e:
        rel.falha("carrega o cadastro", f"{type(e).__name__}: {e}")
        print("\n      -> PK ou FK violada costuma significar cadastro duplicado ou orfao.")
        print("        As consultas de diagnostico estao no fim de ddl_input_migracao_01.sql.\n")
        return False

    rel.ok("carrega o cadastro", f"{total} linhas em {s_in}")
    if descartadas:
        rel.ok("colunas da fixture sem coluna no DDL (ignoradas)", "; ".join(descartadas)[:90])
    return True


def passo_run_request(conn, rel, s_ctrl, params):
    import json
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {s_ctrl}.run_status WHERE run_id = %s;", (RUN_ID,))
                cur.execute(f"DELETE FROM {s_ctrl}.run_request WHERE run_id = %s;", (RUN_ID,))
                cur.execute(
                    f"""INSERT INTO {s_ctrl}.run_request (run_id, unidade, params, solicitado_por)
                        VALUES (%s, %s, %s::jsonb, %s);""",
                    (RUN_ID, params.get("UNIDADE"), json.dumps(params), "smoke-test"))
        rel.ok("insere controle.run_request")
        return True
    except Exception as e:
        rel.falha("insere controle.run_request", f"{type(e).__name__}: {e}")
        return False


def passo_rodar(conn, rel, pg, s_in, s_ctrl, s_pub, rotulo="roda o job"):
    from otimizador.aplicacao.job_databricks import rodar
    try:
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            r = rodar(RUN_ID, pg, schema_input=s_in, schema_ctrl=s_ctrl, schema_pub=s_pub)
    except Exception as e:
        rel.falha(rotulo, f"{type(e).__name__}: {e}")
        print("\n" + traceback.format_exc())
        return None
    if r.get("status") != "SUCESSO":
        rel.falha(rotulo, f"status={r.get('status')} · {str(r.get('resumo'))[:60]}")
        print("\n--- log do job ---\n" + saida.getvalue()[-2500:])
        return None
    rel.ok(rotulo, f"status=SUCESSO")

    st = _um(conn, f"SELECT status FROM {s_ctrl}.run_status WHERE run_id = %s;", (RUN_ID,))
    rel.ok("controle.run_status", "SUCESSO") if st == "SUCESSO" else \
        rel.falha("controle.run_status", f"esperado SUCESSO, veio {st!r}")

    n_diag = _um(conn, f"SELECT count(*) FROM {s_ctrl}.run_diagnostico WHERE run_id = %s;", (RUN_ID,))
    rel.ok("diagnostico gravado", f"{n_diag} checagens") if n_diag else \
        rel.falha("diagnostico gravado", "nenhuma linha")
    return r


def passo_reconciliar(conn, rel, s_pub):
    """As mesmas reconciliacoes do portao, agora contra o que FICOU GRAVADO."""
    p = f"{s_pub}.otim_"
    checagens = [
        ("cabecalho tem 1 linha",
         f"SELECT count(*) FROM {p}meta WHERE run_id = %s", lambda v: v == 1, "1"),
        ("VPL: meta = soma por sub-bacia",
         f"""SELECT abs(m.vpl - coalesce((SELECT sum(vpl) FROM {p}subbacia WHERE run_id = %s), 0))
               FROM {p}meta m WHERE m.run_id = %s""", lambda v: v is not None and v <= 0.01, "<= R$ 0,01"),
        ("CAPEX: meta = soma por ano",
         f"""SELECT abs(m.capex_total - coalesce((SELECT sum(capex) FROM {p}ano WHERE run_id = %s), 0))
               FROM {p}meta m WHERE m.run_id = %s""", lambda v: v is not None and v <= 0.01, "<= R$ 0,01"),
        ("CAPEX: ano = mes",
         f"""SELECT abs(coalesce((SELECT sum(capex) FROM {p}ano WHERE run_id = %s), 0)
                      - coalesce((SELECT sum(capex_mes) FROM {p}mes WHERE run_id = %s), 0))""",
         lambda v: v is not None and v <= 0.01, "<= R$ 0,01"),
        ("rateio: fracoes somam 1 por obra",
         f"""SELECT count(*) FROM (SELECT obra_id FROM {p}dependencia WHERE run_id = %s
                GROUP BY 1 HAVING abs(sum(fracao_rateio) - 1) > 1e-6) t""",
         lambda v: v == 0, "0 obras fora"),
        ("teto anual definido e respeitado",
         f"""SELECT count(*) FROM {p}ano
              WHERE run_id = %s AND (teto_capex IS NULL OR excesso > 1)""",
         lambda v: v == 0, "0 anos"),
        ("view de historico responde",
         f"SELECT count(*) FROM {s_pub}.otim_vw_historico WHERE run_id = %s",
         lambda v: v == 1, "1 linha"),
    ]
    for nome, sql, valida, esperado in checagens:
        try:
            args = (RUN_ID,) * sql.count("%s")
            v = _um(conn, sql, args)
            rel.ok(nome, f"{v}") if valida(v) else rel.falha(nome, f"veio {v}, esperado {esperado}")
        except Exception as e:
            rel.falha(nome, f"{type(e).__name__}: {e}")


def _contagens(conn, s_pub):
    tabelas = ["meta", "obra", "subbacia", "subbacia_ano", "sistema", "dependencia",
               "ano", "mes", "cidade", "cidade_ano", "cobertura", "meta_cobertura", "paridade"]
    return {t: _um(conn, f"SELECT count(*) FROM {s_pub}.otim_{t} WHERE run_id = %s;", (RUN_ID,))
            for t in tabelas}


# --------------------------------------------------------------------- principal
def main(argv=None):
    ap = argparse.ArgumentParser(description="Smoke test do pipeline contra um Postgres real.")
    ap.add_argument("--pg", default=os.environ.get("OTIMIZADOR_PG_TESTE"),
                    help="URL do Postgres (ou variavel OTIMIZADOR_PG_TESTE)")
    ap.add_argument("--fixture", default=FIXTURE_PADRAO)
    ap.add_argument("--schemas-reais", action="store_true",
                    help="usa input/controle/public em vez de smoke_*")
    ap.add_argument("--forcar", action="store_true",
                    help="prossegue mesmo se os schemas ja existirem (APAGA)")
    ap.add_argument("--manter", action="store_true",
                    help="nao derruba os schemas no fim")
    a = ap.parse_args(argv)

    if not a.pg:
        print("ERRO: informe --pg ou defina OTIMIZADOR_PG_TESTE.\n"
              "  docker run --rm -d -p 5433:5432 -e POSTGRES_PASSWORD=teste --name pg-otim postgres:16\n"
              '  python main.py smoke --pg "postgresql://postgres:teste@localhost:5433/postgres"')
        return 2

    s_in, s_ctrl, s_pub = (("input", "controle", "public") if a.schemas_reais
                           else ("smoke_input", "smoke_controle", "smoke_public"))
    schemas = [s_in, s_ctrl, s_pub]

    print("=" * 78)
    print(f"  SMOKE TEST — schemas {', '.join(schemas)}")
    print(f"  fixture: {a.fixture}")
    print("=" * 78)

    try:
        conn = _conn(a.pg)
    except Exception as e:
        print(f"  [FALHA] conecta no Postgres: {type(e).__name__}: {e}")
        return 1

    rel = Relatorio()
    try:
        rel.ok("conecta no Postgres", _um(conn, "SELECT version()")[:48])

        if _tem_dados(conn, schemas) and not a.forcar:
            print(f"\n  ABORTADO: os schemas {schemas} ja existem neste banco.\n"
                  f"  Use --forcar para APAGAR e recriar, ou aponte para um banco limpo.\n")
            return 2
        _derrubar(conn, schemas)

        if not passo_ddl(conn, rel, s_in, s_ctrl, s_pub):
            return 1
        if not passo_carga(conn, rel, s_in, a.fixture):
            return 1
        if not passo_run_request(conn, rel, s_ctrl, PARAMS_RODADA):
            return 1

        print("\n  --- 1a rodada ---")
        if passo_rodar(conn, rel, a.pg, s_in, s_ctrl, s_pub) is None:
            return 1
        passo_reconciliar(conn, rel, s_pub)
        antes = _contagens(conn, s_pub)
        rel.ok("resultado publicado", f"{sum(antes.values())} linhas em {len(antes)} tabelas")

        print("\n  --- 2a rodada, MESMO run_id (idempotencia) ---")
        if passo_rodar(conn, rel, a.pg, s_in, s_ctrl, s_pub, "roda de novo") is None:
            return 1
        depois = _contagens(conn, s_pub)
        if depois == antes:
            rel.ok("republicacao nao duplicou", "contagens identicas em 13 tabelas")
        else:
            dif = {t: (antes[t], depois[t]) for t in antes if antes[t] != depois[t]}
            rel.falha("republicacao nao duplicou", f"antes/depois: {dif}")
        n_meta = _um(conn, f"SELECT count(*) FROM {s_pub}.otim_meta;")
        rel.ok("um run_id = uma rodada", "1 linha no cabecalho") if n_meta == 1 else \
            rel.falha("um run_id = uma rodada", f"{n_meta} linhas em otim_meta")

        print()
        rel.resumo()
        if a.manter:
            print(f"  schemas mantidos para inspecao: {', '.join(schemas)}")
        return 0 if rel.tudo_bem else 1
    finally:
        try:
            if not a.manter:
                _derrubar(conn, schemas)
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
