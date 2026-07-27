# =============================================================================
#  PUBLICACAO DOS RESULTADOS — CAMADA DE SERVICO
#  Fecha o ciclo de producao:
#
#      Databricks roda  ->  publicar()  ->  Blob (ADLS)  +  PostgreSQL  ->  notifica o backend
#                                                                              |
#                                              backend (AKS) atualiza o historico e serve o front
#
#  Divisao de responsabilidade:
#    • PostgreSQL  — tudo que o FRONT consulta de forma interativa. Modelado para
#                    leitura direta: 1 tabela cabecalho para a lista de historico
#                    (sem join) e tabelas de detalhe indexadas por run_id.
#    • Blob (ADLS) — o que e pesado e raramente consultado: snapshot do banco de
#                    entrada, copia integral em parquet e artefatos exportados.
#                    E a camada de reproducao/auditoria.
#
#  Uso:
#      import persistencia as P, publicacao as PUB
#      tabs = P.materializar(cen, res, banco=BANCO, params={...})
#      PUB.publicar(tabs,
#                   pg='postgresql://user:senha@host:5432/otimizador',
#                   blob='abfss://dados@conta.dfs.core.windows.net/otimizador/',
#                   notificar={'webhook': 'https://api.interno/otimizacoes/concluida'},
#                   rotulo='Cenario base 15 anos', usuario='lucio.rosa@peers.com.br')
# =============================================================================
import contextlib as _contextlib
import datetime as _dt
import json as _js
import os as _os

import pandas as pd

# tabelas que vao para o Postgres (as snapshot__* ficam so no blob)
TABELAS_SERVICO = [
    "run_meta", "run_obra", "run_subbacia", "run_subbacia_ano", "run_sistema",
    "run_dependencia",
    "run_ano", "run_mes",
    "run_cidade", "run_cidade_ano", "run_cobertura", "run_meta_cobertura",
    "run_paridade", "run_auditoria",
]
PREFIXO = "otim_"

# chaves e indices pensados no que o front consulta
CHAVES = {
    "run_meta":          ("run_id",),
    "run_obra":          ("run_id", "obra_id"),
    "run_subbacia":      ("run_id", "sub_bacia"),
    "run_subbacia_ano":  ("run_id", "sub_bacia", "ano"),
    "run_sistema":       ("run_id", "sistema"),
    "run_dependencia":   ("run_id", "obra_id", "sub_bacia"),
    "run_ano":           ("run_id", "ano"),
    "run_mes":           ("run_id", "mes_indice"),
    "run_cidade":        ("run_id", "cidade"),
    "run_cidade_ano":    ("run_id", "cidade", "ano"),
    "run_cobertura":     ("run_id", "cidade", "ano"),
    "run_meta_cobertura": ("run_id", "cidade", "ano"),
    "run_paridade":      ("run_id", "cidade", "ano"),
    "run_auditoria":     None,                       # sem PK natural (violacoes + reparos)
}
INDICES = {
    "run_obra":        [("run_id", "cidade"), ("run_id", "status"), ("run_id", "categoria_motivo")],
    "run_subbacia":    [("run_id", "cidade"), ("run_id", "faturando")],
    "run_dependencia": [("run_id", "sub_bacia"), ("run_id", "obra_id")],
    "run_subbacia_ano": [("run_id", "sub_bacia"), ("run_id", "cidade", "ano")],
    "run_cobertura":   [("run_id", "cidade")],
    "run_sistema":     [("run_id", "cidade")],
    "run_mes":         [("run_id", "ano")],
}
COLUNAS_JSONB = {"capex_componentes", "params_extra", "peso_cidade", "orcamento_por_ano",
                 "obrig_desconsideradas", "detalhe"}
# tipos que nao dao para inferir do dtype (coluna toda nula, data em texto, etc.)
TIPOS_FIXOS = {"data_hora": "TIMESTAMPTZ", "milp_bound": "DOUBLE PRECISION",
               "saldo_potencial": "DOUBLE PRECISION", "tempo_s": "DOUBLE PRECISION",
               "cobertura_final_pct": "DOUBLE PRECISION", "latitude": "DOUBLE PRECISION",
               "longitude": "DOUBLE PRECISION", "status_execucao": "TEXT",
               "rotulo": "TEXT", "usuario": "TEXT", "erro": "TEXT", "blob_uri": "TEXT",
               "gasto": "DOUBLE PRECISION", "teto": "DOUBLE PRECISION",
               "excesso": "DOUBLE PRECISION", "ano": "BIGINT",
               "cobertura_ligacoes": "DOUBLE PRECISION", "deficit_ligacoes": "DOUBLE PRECISION",
               "atingida": "BOOLEAN", "wacc_receita": "DOUBLE PRECISION",
               "primeiro_modulo_mes": "BIGINT", "ocupacao_pct": "DOUBLE PRECISION",
               "quantidade": "DOUBLE PRECISION", "preco_unitario": "DOUBLE PRECISION",
               "unidade": "TEXT", "densidade_economias": "DOUBLE PRECISION",
               "densidade_populacao": "DOUBLE PRECISION",
               "fator_unidade_cobertura": "DOUBLE PRECISION",
               "potencial_crescimento": "DOUBLE PRECISION", "curva_adocao": "TEXT",
               "receita_efeito_base": "DOUBLE PRECISION", "receita_total": "DOUBLE PRECISION",
               "ebitda": "DOUBLE PRECISION", "ebitda_acumulado": "DOUBLE PRECISION",
               "ebitda_margem_pct": "DOUBLE PRECISION",
               # colunas que ficam TODAS NULAS em rodada pequena (sem obrigatoria, sem ETE
               # faseada, sem potencial residual) e seriam inferidas TEXT. O tipo real vem
               # de quem as escreve, em persistencia.py — nao de amostra:
               "foco_cobertura": "DOUBLE PRECISION",          # cen.foco_cobertura: float 0..1
               "obrig_total": "BIGINT", "obrig_construidas": "BIGINT",   # contagens
               "obrig_ano_plano": "BIGINT",                   # ano do plano para a obrigatoria
               "faturando": "BOOLEAN",                        # bool(...) em obra/subbacia/ano
               "mes_inicio_faturamento": "BIGINT",            # indice de mes
               "pot_vp_receita": "DOUBLE PRECISION", "pot_vp_capex_solo": "DOUBLE PRECISION",
               "pot_vp_capex_rateado": "DOUBLE PRECISION", "pot_vp_opex": "DOUBLE PRECISION",
               "pot_saldo_solo": "DOUBLE PRECISION", "pot_saldo_rateado": "DOUBLE PRECISION",
               "pot_obras_faltantes": "BIGINT",               # len(inicio_r): contagem
               "capex_modulo": "DOUBLE PRECISION", "capex_terreno": "DOUBLE PRECISION",
               "capex_modulos_construidos": "DOUBLE PRECISION"}   # sum(m.capex ...)
# colunas de servico que o front consome e que nao vem do engine
COLUNAS_SERVICO = [("status_execucao", "CONCLUIDO"), ("rotulo", None), ("usuario", None),
                   ("erro", None), ("blob_uri", None), ("cobertura_final_pct", None),
                   ("tempo_s", None)]


def _garantir_colunas_servico(tabs):
    """Assegura que run_meta tem as colunas que o front espera, mesmo que a rodada
    tenha vindo so do engine. Idempotente."""
    m = tabs.get("run_meta")
    if m is None:
        return tabs
    for col, val in COLUNAS_SERVICO:
        if col not in m.columns:
            m[col] = val
    if m["cobertura_final_pct"].isna().all():
        cid = tabs.get("run_cidade")
        if cid is not None and len(cid) and cid["universo"].notna().any():
            u = cid["universo"].fillna(0).sum()
            if u:
                m["cobertura_final_pct"] = (
                    (cid["cobertura_final_pct"].fillna(0) * cid["universo"].fillna(0)).sum() / u)
    return tabs


# ----------------------------------------------------------------------- DDL
def _tipo_pg(serie, coluna):
    if coluna in COLUNAS_JSONB:
        return "JSONB"
    if coluna in TIPOS_FIXOS:
        return TIPOS_FIXOS[coluna]
    k = serie.dtype.kind
    if k == "b":
        return "BOOLEAN"
    if k == "i":
        return "BIGINT"
    if k == "f":
        return "DOUBLE PRECISION"
    return "TEXT"


def ddl_postgres(tabs, schema="public", incluir_views=True):
    """Script DDL completo, derivado das tabelas materializadas. Entregue ao time de
    backend/DBA como migration — ele reflete exatamente o que publicar() escreve."""
    _garantir_colunas_servico(tabs)
    out = [f"-- DDL do otimizador de CAPEX — gerado em {_dt.datetime.now():%Y-%m-%d %H:%M}",
           f"CREATE SCHEMA IF NOT EXISTS {schema};", ""]
    for nome in TABELAS_SERVICO:
        df = tabs.get(nome)
        if df is None:
            continue
        tab = f"{schema}.{PREFIXO}{nome[4:]}"
        cols = [f"    {c} {_tipo_pg(df[c], c)}" for c in df.columns]
        pk = CHAVES.get(nome)
        if pk:
            cols.append(f"    PRIMARY KEY ({', '.join(pk)})")
        if nome != "run_meta":
            cols.append(f"    , CONSTRAINT fk_{nome[4:]}_run FOREIGN KEY (run_id) "
                        f"REFERENCES {schema}.{PREFIXO}meta(run_id) ON DELETE CASCADE")
        corpo = ",\n".join(cols).replace(",\n    , CONSTRAINT", ",\n    CONSTRAINT")
        out.append(f"CREATE TABLE IF NOT EXISTS {tab} (\n{corpo}\n);")
        for ix in INDICES.get(nome, []):
            out.append(f"CREATE INDEX IF NOT EXISTS ix_{nome[4:]}_{'_'.join(ix)} "
                       f"ON {tab} ({', '.join(ix)});")
        out.append("")
    # cabecalho do historico: uma consulta so, sem join
    out.append(f"CREATE INDEX IF NOT EXISTS ix_meta_data ON {schema}.{PREFIXO}meta (data_hora DESC);")
    out.append(f"CREATE INDEX IF NOT EXISTS ix_meta_status ON {schema}.{PREFIXO}meta (status_execucao);")
    out.append("")
    if incluir_views:
        out.append(_views_sql(schema))
    return "\n".join(out)


def _views_sql(schema):
    p = f"{schema}.{PREFIXO}"
    return f"""-- ---------------------------------------------------------------- VIEWS
-- 1) Lista de historico: e o que a tela inicial consome, sem nenhum join.
CREATE OR REPLACE VIEW {p}vw_historico AS
SELECT run_id, rotulo, usuario, data_hora, status_execucao, milp_status,
       anos_capex, orcamento_total, vpl, capex_total, obras_construidas, obras_total,
       subbacias_faturando, subbacias_total, cobertura_final_pct,
       metas_total, metas_nao_atingidas, vp_efeito_base, auditoria_ok, tempo_s
FROM {p}meta
ORDER BY data_hora DESC;

-- 2) Obras que ficaram de fora, com o diagnostico ja montado.
CREATE OR REPLACE VIEW {p}vw_obra_fora AS
SELECT run_id, obra_id, tipo, cidade, no AS sub_bacia, capex, ligacoes,
       categoria_motivo, elo_que_trava, saldo_potencial, motivo
FROM {p}obra
WHERE status = 'FORA';

-- 3) Topologia: arestas ja enriquecidas com os atributos da obra.
CREATE OR REPLACE VIEW {p}vw_topologia AS
SELECT d.run_id, d.sub_bacia, d.obra_id, d.obra_tipo, d.cidade, d.sistema,
       d.fracao_rateio, d.capex_rateado, d.n_dependentes,
       d.obra_construida, d.sub_bacia_faturando,
       o.capex, o.data_inicio, o.data_pronta, o.responsavel, o.status
FROM {p}dependencia d
JOIN {p}obra o ON o.run_id = d.run_id AND o.obra_id = d.obra_id;
"""


# ------------------------------------------------------------------ POSTGRES
def _conectar(pg):
    """Devolve (conn, proprio). `proprio` = True quando fomos NOS que abrimos a conexao.

    Aceita uma conexao ja aberta — e assim que o job junta publicacao e status numa
    transacao so (ver `_transacao`). psycopg2 e dependencia dura (`execute_values` ja
    exige): o antigo fallback para `create_engine(pg).raw_connection()` era pior que
    inutil, porque o `with conn:` de uma PoolProxiedConnection chama close() em vez de
    commit — a escrita sumia sem erro nenhum.

    DSN aceito: `postgresql://user:senha@host:5432/db` (o formato do Secret Scope precisa
    servir tambem ao SQLAlchemy de `carregar_postgres`; `postgresql+psycopg2://` NAO serve
    aqui, psycopg2 rejeita o sufixo do dialeto).
    """
    if hasattr(pg, "cursor"):
        return pg, False
    import psycopg2                      # requirements-prod.txt: psycopg2-binary
    return psycopg2.connect(pg), True


@_contextlib.contextmanager
def _transacao(pg):
    """Um cursor dentro de UMA transacao.

    Se abrimos a conexao: commit no fim, rollback em qualquer excecao, e fechamos.
    Se a conexao veio de fora: quem manda no commit e o chamador — e o que permite
    publicar as run_* e marcar SUCESSO no MESMO commit.
    """
    conn, proprio = _conectar(pg)
    try:
        with conn.cursor() as cur:
            yield cur
        if proprio:
            conn.commit()
    except Exception:
        if proprio:
            conn.rollback()
        raise
    finally:
        if proprio:
            try:
                conn.close()
            except Exception:
                pass


def publicar_postgres(tabs, pg, schema="public", criar=True, substituir=True, verbose=True):
    """Escreve as tabelas de servico no Postgres, de forma IDEMPOTENTE e em UMA
    transacao: se qualquer coisa falhar, nada e gravado e o historico nao muda.
    substituir=True apaga a rodada antes de inserir (seguro para retry do job)."""
    from psycopg2.extras import execute_values
    rid = tabs["run_meta"]["run_id"].iloc[0]
    escritos = []
    with _transacao(pg) as cur:
        if criar:
            cur.execute(ddl_postgres(tabs, schema=schema))
        if substituir:                     # CASCADE apaga os detalhes junto
            cur.execute(f"DELETE FROM {schema}.{PREFIXO}meta WHERE run_id = %s;", (rid,))
        for nome in TABELAS_SERVICO:       # run_meta primeiro (FK)
            df = tabs.get(nome)
            if df is None or len(df) == 0:
                continue
            tab = f"{schema}.{PREFIXO}{nome[4:]}"
            cols = list(df.columns)
            dados = []
            for _, r in df.iterrows():
                linha = []
                for c in cols:
                    v = r[c]
                    if not isinstance(v, (list, dict)) and pd.isna(v):
                        v = None
                    elif c in COLUNAS_JSONB and not isinstance(v, str):
                        v = _js.dumps(v, ensure_ascii=False, default=str)
                    elif hasattr(v, "item"):
                        v = v.item()
                    linha.append(v)
                dados.append(tuple(linha))
            execute_values(cur,
                           f"INSERT INTO {tab} ({', '.join(cols)}) VALUES %s",
                           dados, page_size=1000)
            escritos.append((tab, len(dados)))
    if verbose:
        print(f"Postgres: {len(escritos)} tabela(s) gravada(s) (run_id={rid})")
        for tab, n in escritos:
            print(f"  {tab:<40}{n:>9,} linhas")
    return escritos


def marcar_status(pg, run_id, status, erro=None, schema="public"):
    """Atualiza o status da rodada no cabecalho (EXECUTANDO / CONCLUIDO / ERRO)."""
    with _transacao(pg) as cur:
        cur.execute(
            f"UPDATE {schema}.{PREFIXO}meta SET status_execucao=%s, erro=%s WHERE run_id=%s;",
            (status, erro, run_id))
    return status


def marcar_status_controle(pg, run_id, status, erro=None, schema="controle"):
    """Status do CICLO DO JOB (PENDENTE/RODANDO/SUCESSO/FALHOU_QUALIDADE/ERRO) na tabela de
    controle — existe ANTES de publicar (o run_meta so aparece depois). Upsert por run_id."""
    with _transacao(pg) as cur:
        cur.execute(
            f"""INSERT INTO {schema}.run_status (run_id, status, erro, atualizado_em)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (run_id) DO UPDATE
                SET status=EXCLUDED.status, erro=EXCLUDED.erro, atualizado_em=now();""",
            (run_id, status, erro))
    return status


def gravar_diagnostico(pg, run_id, relatorio, schema="controle"):
    """Grava o relatorio do portao de qualidade (uma linha por checagem) em
    controle.run_diagnostico. Idempotente: apaga o diagnostico anterior do run_id."""
    from psycopg2.extras import execute_values
    with _transacao(pg) as cur:
        cur.execute(f"DELETE FROM {schema}.run_diagnostico WHERE run_id=%s;", (run_id,))
        dados = [(run_id, r.get("check"), r.get("nivel"), bool(r.get("ok")), r.get("detalhe"))
                 for r in (relatorio or [])]
        if dados:
            execute_values(cur,
                f"INSERT INTO {schema}.run_diagnostico (run_id, checagem, nivel, ok, detalhe) VALUES %s",
                dados, page_size=500)
    return len(relatorio or [])


# ---------------------------------------------------------------------- BLOB
def publicar_blob(tabs, destino, formato="parquet", incluir_snapshot=True, verbose=True):
    """Grava a copia integral no ADLS Gen2 / DBFS / disco — inclusive as abas do banco
    de entrada, que NAO vao para o Postgres. E a camada de reproducao e auditoria."""
    import persistencia as _P
    alvo = {k: v for k, v in tabs.items()
            if incluir_snapshot or not k.startswith("snapshot__")}
    return _P.salvar(alvo, destino, formato=formato, verbose=verbose)


def uri_blob(destino, run_id):
    return str(destino).rstrip("/") + f"/run_id={run_id}"


# --------------------------------------------------------------- NOTIFICACAO
def _payload(tabs, blob_uri=None, extra=None):
    m = tabs["run_meta"].iloc[0]
    p = {
        "evento": "otimizacao.concluida",
        "run_id": m["run_id"],
        "data_hora": m["data_hora"],
        "status": "CONCLUIDO",
        "regional": m.get("regional"),
        "anos_capex": int(m["anos_capex"]) if pd.notna(m.get("anos_capex")) else None,
        "orcamento_total": float(m["orcamento_total"]) if pd.notna(m.get("orcamento_total")) else None,
        "vpl": float(m["vpl"]) if pd.notna(m.get("vpl")) else None,
        "capex_total": float(m["capex_total"]) if pd.notna(m.get("capex_total")) else None,
        "obras_construidas": int(m["obras_construidas"]) if pd.notna(m.get("obras_construidas")) else None,
        "subbacias_faturando": int(m["subbacias_faturando"]) if pd.notna(m.get("subbacias_faturando")) else None,
        "metas_nao_atingidas": int(m["metas_nao_atingidas"]) if pd.notna(m.get("metas_nao_atingidas")) else None,
        "auditoria_ok": bool(m.get("auditoria_ok", True)),
        "blob_uri": blob_uri,
    }
    if extra:
        p.update(extra)
    return p


def notificar_webhook(url, payload, token=None, timeout=15):
    import urllib.request
    dados = _js.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req = urllib.request.Request(url, data=dados, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "ignore")[:400]


def notificar_service_bus(conn_str, fila_ou_topico, payload, e_topico=False):
    """Publica o evento no Azure Service Bus — o caminho preferido, porque desacopla
    o job do Databricks da disponibilidade do backend."""
    from azure.servicebus import ServiceBusClient, ServiceBusMessage
    msg = ServiceBusMessage(_js.dumps(payload, ensure_ascii=False, default=str),
                            content_type="application/json",
                            subject="otimizacao.concluida",
                            message_id=str(payload.get("run_id")))
    with ServiceBusClient.from_connection_string(conn_str) as cli:
        emissor = (cli.get_topic_sender(fila_ou_topico) if e_topico
                   else cli.get_queue_sender(fila_ou_topico))
        with emissor:
            emissor.send_messages(msg)
    return True


# ------------------------------------------------------------- ORQUESTRACAO
def publicar(tabs, pg=None, blob=None, notificar=None, schema="public",
             rotulo=None, usuario=None, criar_schema=True, verbose=True,
             status_controle=None):
    """Fecha o ciclo na ordem segura:
         1. blob      (barato de refazer; se falhar, nada foi prometido)
         2. postgres  (transacional; so aqui a rodada passa a existir para o front)
         3. notifica  (SEMPRE depois do commit — nunca antes)

    notificar aceita {'webhook': url, 'token': ...} e/ou
                     {'service_bus': conn_str, 'fila': nome, 'topico': nome}

    status_controle: `(run_id, schema_controle)`. Quando informado, o `SUCESSO` em
    `<schema_controle>.run_status` e gravado na MESMA transacao das run_*, para o estado
    observavel nunca divergir do dado publicado. E o que o job usa.

    Devolve o payload enviado ao backend."""
    _garantir_colunas_servico(tabs)
    m = tabs["run_meta"]
    if rotulo is not None:
        m["rotulo"] = rotulo
    if usuario is not None:
        m["usuario"] = usuario
    rid = m["run_id"].iloc[0]

    burl = None
    if blob:
        publicar_blob(tabs, blob, verbose=verbose)
        burl = uri_blob(blob, rid)
        m["blob_uri"] = burl
    elif "blob_uri" not in m.columns:
        m["blob_uri"] = None

    if pg:
        conn, proprio = _conectar(pg)
        try:
            # a conexao vai adiante: `publicar_postgres` NAO commita nem fecha conexao que
            # recebeu pronta, entao as run_* e o status entram na MESMA transacao.
            publicar_postgres(tabs, conn, schema=schema, criar=criar_schema, verbose=verbose)
            if status_controle:
                rid_ctrl, schema_ctrl = status_controle
                marcar_status_controle(conn, rid_ctrl, "SUCESSO", schema=schema_ctrl)
            if proprio:
                conn.commit()
        except Exception:
            if proprio:
                conn.rollback()
            raise
        finally:
            if proprio:
                try:
                    conn.close()
                except Exception:
                    pass

    pay = _payload(tabs, blob_uri=burl, extra={"rotulo": rotulo, "usuario": usuario})
    if notificar:
        if notificar.get("service_bus"):
            notificar_service_bus(notificar["service_bus"],
                                  notificar.get("topico") or notificar.get("fila"),
                                  pay, e_topico=bool(notificar.get("topico")))
            if verbose:
                print(f"Service Bus: evento publicado (run_id={rid})")
        if notificar.get("webhook"):
            st, corpo = notificar_webhook(notificar["webhook"], pay, notificar.get("token"))
            if verbose:
                print(f"Webhook: HTTP {st}  {corpo[:120]}")
    if verbose:
        print(f"\nrodada {rid} publicada e disponivel para o front.")
    return pay


def contrato_backend(schema="public"):
    """Resumo do contrato para o time de backend: o que consultar em cada tela."""
    p = f"{schema}.{PREFIXO}"
    return f"""CONTRATO DE LEITURA — backend (AKS) -> PostgreSQL

TELA                         CONSULTA
---------------------------  --------------------------------------------------
Historico de otimizacoes     SELECT * FROM {p}vw_historico LIMIT 50 OFFSET :n
Cabecalho de uma rodada      SELECT * FROM {p}meta WHERE run_id = :run
Painel geral (graficos)      SELECT * FROM {p}ano       WHERE run_id = :run ORDER BY ano
                             SELECT * FROM {p}mes       WHERE run_id = :run ORDER BY mes_indice
Lista de obras               SELECT * FROM {p}obra      WHERE run_id = :run
                               [+ AND cidade = :cidade] [+ AND status = :status]
Obras fora + diagnostico     SELECT * FROM {p}vw_obra_fora WHERE run_id = :run
Deep dive da sub-bacia       SELECT * FROM {p}subbacia  WHERE run_id = :run AND sub_bacia = :sb
Topologia ate a ETE          SELECT * FROM {p}vw_topologia WHERE run_id = :run AND sub_bacia = :sb
Visao da cidade              SELECT * FROM {p}cidade    WHERE run_id = :run
                             SELECT * FROM {p}cidade_ano WHERE run_id = :run AND cidade = :cid
Cobertura no tempo           SELECT * FROM {p}cobertura WHERE run_id = :run ORDER BY cidade, ano
Metas                        SELECT * FROM {p}meta_cobertura WHERE run_id = :run
Paridade                     SELECT * FROM {p}paridade  WHERE run_id = :run
Auditoria do teto            SELECT * FROM {p}auditoria WHERE run_id = :run

EVENTO recebido do Databricks (Service Bus / webhook):
  {{"evento":"otimizacao.concluida","run_id":"...","status":"CONCLUIDO",
    "vpl":...,"capex_total":...,"obras_construidas":...,"blob_uri":"abfss://..."}}
  -> o backend so precisa invalidar o cache da lista; os dados ja estao commitados.

OBSERVACOES
  • Todas as tabelas de detalhe tem FK para {p}meta com ON DELETE CASCADE:
    apagar a rodada no cabecalho remove tudo.
  • A publicacao e idempotente: reprocessar o mesmo run_id apaga e regrava.
  • Volume por rodada: ~7 mil linhas. Pagine a lista de obras, o resto cabe inteiro.
  • O snapshot do banco de entrada NAO esta no Postgres — fica no blob (blob_uri).
"""
