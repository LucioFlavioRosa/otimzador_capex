"""FASE 4 — Orquestracao do job no Databricks (entrypoint FINO).

Fluxo de UMA rodada (idempotente por run_id, retriavel):

    1. le a run_request (parametros da celula PARAMETROS) do Postgres  -- ANTES do status
    2. marca RODANDO
    3. carrega o input do Postgres  -> Cenario         (Fase 2)  + exige teto de CAPEX
    4. resolve com o OR-Tools                            (motor + solver)
    5. materializa em tabelas, com o run_id DA RODADA    (persistencia)
    6. PORTAO DE QUALIDADE                               (Fase 3) -- falhou? nao publica
    7. publica: blob -> [run_* + SUCESSO num commit so] -> notifica

Toda a logica pesada vive nos modulos; aqui so orquestra e trata erro. A conexao do
Postgres vem de um Databricks Secret Scope (nunca hardcoded).

Uso no Databricks (notebook de 1 celula OU wheel entrypoint):

    from job_databricks import rodar
    rodar(run_id=dbutils.widgets.get("run_id"),
          pg_url=dbutils.secrets.get("otimizador","pg_url"),
          blob=dbutils.widgets.get("blob_uri"),                   # ADLS p/ os snapshots
          service_bus=dbutils.secrets.get("otimizador","sb_conn"))

(O `blob` e um caminho, nao uma credencial: pode vir de widget/config. O acesso ao ADLS e
que precisa de segredo — e ele fica na configuracao do cluster, nao aqui.)
"""
from __future__ import annotations
import json
import traceback


def _ler_run_request(pg_url, run_id, schema="controle"):
    """Le a linha de run_request (parametros da rodada) como dict."""
    import pandas as pd
    from sqlalchemy import create_engine, text
    # bind no estilo do SQLAlchemy (:rid). O `%(rid)s` so funcionava por acidente do
    # paramstyle pyformat do psycopg2: o pandas embrulha a string em text(), que so
    # entende `:nome` — com qualquer outro driver o `%` chega literal e o SQL quebra.
    eng = create_engine(pg_url)
    try:
        df = pd.read_sql(text(f'SELECT * FROM "{schema}".run_request WHERE run_id = :rid'),
                         eng, params={"rid": run_id})
    finally:
        eng.dispose()                     # senao o pool fica aberto ate o fim do job
    if df is None or len(df) == 0:
        raise RuntimeError(f"run_request nao encontrada para run_id={run_id}")
    row = df.iloc[0].to_dict()
    # 'params' e um JSONB com os parametros da celula PARAMETROS
    p = row.get("params")
    return json.loads(p) if isinstance(p, str) else (p or {})


# chave do run_request (JSONB)  ->  kwarg do ler_banco
MAPA_PARAMS = {
    "ORCAMENTO":            "orcamento",             "ORCAMENTO_TOTAL": "orcamento_total",
    "HORIZONTE_CAPEX":      "horizonte_capex",       "ETE_FASEADA":     "ete_faseada",
    "ETE_FIXO":             "ete_fixo",              "METAS_COBERTURA": "metas_cobertura",
    "PESO_COBERTURA":       "peso_cobertura",        "FOCO_COBERTURA":  "foco_cobertura",
    "PENALIDADE_COBERTURA": "penalidade_cobertura",  "PESO_CIDADE":     "peso_cidade",
    "DATA_INICIO":          "data_inicio",           "REGIONAL":        "regional",
    "UNIDADE":              "unidade",               "CURVA_ADOCAO":    "curva_adocao",
    "BASE_RECEITA":         "base_receita",          "USAR_CTS":        "usar_cts",
    "ANOS_EXTRA_CONCLUSAO": "anos_extra_conclusao",  "INCLUIR_INDUSTRIAL": "incluir_industrial",
}
# chaves que sao do JOB, nao do motor (nao viram kwarg do ler_banco)
CHAVES_DO_JOB = {"USUARIO", "MAX_TIME_S", "WORKERS"}


def _e_ano(k):
    return isinstance(k, str) and k.isdigit() and 1900 <= int(k) <= 2200


def _normalizar_orcamento(v):
    """Converte {"2026": teto} em {2026: teto}.

    JSONB devolve chave de objeto SEMPRE como string, mas o motor so reconhece o
    cronograma por ano se as chaves forem int:

        _orc_cal = isinstance(orcamento,dict) and ... and all(isinstance(k,int) ...)

    Sem esta conversao, {"2026": ...} nao e cronograma: cai no ramo "orcamento por
    unidade", nao encontra a unidade e o teto vira INF — plano sem teto, que estoura no
    CP-SAT. Chave que nao for ano fica intacta (e orcamento por unidade/regional).
    """
    if isinstance(v, dict) and v and all(_e_ano(k) for k in v):
        return {int(k): v[k] for k in v}
    return v


def _exigir_teto_anual(cen):
    """Falha cedo se o Cenario ficou SEM teto anual de CAPEX.

    O motor resolve o teto nesta ordem: parametro ORCAMENTO -> tabela `input.orcamento`
    -> INF. Com INF o CP-SAT estoura em `int(round(inf))` la dentro do solver, com uma
    mensagem que nao diz o que faltou.

    Esta checagem fica DEPOIS da carga de proposito: e o que permite o fallback pela
    tabela `input.orcamento` continuar funcionando. E ORCAMENTO_TOTAL nao entra na conta —
    ele limita o total da janela, mas a restricao anual continua lendo `cen.orc`.
    """
    import math
    sem_teto = sorted(str(reg) for reg, tetos in (getattr(cen, "orc", {}) or {}).items()
                      if not all(math.isfinite(float(t)) for t in tetos))
    if sem_teto:
        raise ValueError(
            f"sem teto anual de CAPEX para {sem_teto}: informe ORCAMENTO no run_request "
            f"(numero, {{ano: teto}} ou {{unidade: teto}}) ou preencha input.orcamento. "
            f"ORCAMENTO_TOTAL sozinho nao define o teto por ano.")


def _params_para_ler_banco(p):
    """Traduz o payload da run_request para os kwargs do ler_banco/carregar_postgres.

    REGRA: chave ausente NAO vira default do job — simplesmente nao e repassada, e o
    proprio `ler_banco` aplica o default dele. E o que garante que o job e o caminho
    Excel resolvam o MESMO problema. (Os defaults antigos do job divergiam do motor:
    ete_faseada=True vs False, que transforma cada ETE em K obras-modulo; e
    foco_cobertura=1.0 vs None, que satura o peso de cobertura -> objetivo "so cobertura"
    em vez de "so VPL". Um run_request sem essas chaves rodava outro problema.)

    Chave desconhecida e ERRO, nao silencio: um `orcamento` minusculo passaria batido e a
    rodada sairia sem teto de CAPEX.
    """
    desconhecidas = sorted(set(p) - set(MAPA_PARAMS) - CHAVES_DO_JOB)
    if desconhecidas:
        raise ValueError(f"run_request.params com chaves desconhecidas: {desconhecidas} "
                         f"(esperadas: {sorted(set(MAPA_PARAMS) | CHAVES_DO_JOB)})")
    kwargs = {kw: p[chave] for chave, kw in MAPA_PARAMS.items() if chave in p}
    if "orcamento" in kwargs:
        kwargs["orcamento"] = _normalizar_orcamento(kwargs["orcamento"])
    return kwargs


def rodar(run_id, pg_url, blob=None, schema_input="input", schema_ctrl="controle",
          schema_pub="public", service_bus=None, webhook=None, webhook_token=None,
          max_time_s=300, workers=8):
    """Executa a rodada fim-a-fim. Devolve o payload de conclusao (ou levanta apos marcar ERRO).

    DIVISAO DE SAIDA (por design):
      - Postgres (schema `public`): as 14 tabelas de RESULTADO (run_* -> otim_*). E o que o front
        consulta. Lista completa em publicacao.TABELAS_SERVICO.
      - Blob/ADLS (`blob`): a COPIA CONGELADA do input (snapshot__*) + parquet integral. E a camada
        de reproducao/auditoria — nao vai para o Postgres. **Passe o `blob`** (ex.:
        'abfss://dados@conta.dfs.core.windows.net/otimizador/'), senao os snapshots nao sao salvos
        e `otim_meta.blob_uri` fica nulo.

    `max_time_s`/`workers` sao o default do job; o run_request pode sobrescrever por rodada
    (MAX_TIME_S / WORKERS). `webhook` notifica o backend por HTTP, alem do Service Bus.
    """
    import otimizador_capex_v62 as M
    import otimizador_capex_cpsat63 as CP
    import dashboard_otimizador_v2 as D
    import persistencia as P
    import publicacao as PUB
    from carregar_postgres import carregar_postgres
    import qualidade as Q

    D.set_engine(M); P.set_engine(M, D)

    try:
        # 1) le a run_request ANTES de marcar RODANDO: controle.run_status tem FK para
        #    run_request, entao marcar status de um run_id inexistente estouraria na FK e
        #    esconderia o erro util ("run_request nao encontrada para run_id=...").
        p = _ler_run_request(pg_url, run_id, schema=schema_ctrl)
        kw = _params_para_ler_banco(p)

        # 2) so agora o status existe para ser observado
        PUB.marcar_status_controle(pg_url, run_id, "RODANDO")

        # 3) carga do input (Postgres -> Cenario)
        cen = carregar_postgres(pg_url, schema=schema_input, **kw)

        # 3b) teto de CAPEX tem de existir — depois da carga, para o fallback pela tabela
        #     `input.orcamento` valer. Sem isso o CP-SAT estoura convertendo INF em int.
        _exigir_teto_anual(cen)

        # 4) otimizacao
        res = CP.resolver_por_sistema(cen,
                                      max_time_s=p.get("MAX_TIME_S", max_time_s),
                                      workers=p.get("WORKERS", workers))

        # 5) materializacao — o run_id da rodada MANDA: e ele que liga controle.* a
        #    public.otim_*, e e a chave do DELETE idempotente de publicar_postgres. Sem
        #    passar aqui, `materializar` gera um id novo e cada retry PUBLICA DE NOVO em
        #    vez de substituir.
        tabs = P.materializar(cen, res, run_id=run_id,
                              banco=f"postgres://{schema_input}", params=p)

        # 6) PORTAO DE QUALIDADE — antes de publicar
        ok, relatorio, resumo = Q.checar(cen, res, tabs)
        Q.imprimir(relatorio, resumo)
        PUB.gravar_diagnostico(pg_url, run_id, relatorio, schema=schema_ctrl)   # sempre grava o relatorio
        if not ok:
            PUB.marcar_status_controle(pg_url, run_id, "FALHOU_QUALIDADE", erro=resumo)
            return {"run_id": run_id, "status": "FALHOU_QUALIDADE", "resumo": resumo}

        # 7) publicacao: `publicar` mantem a ordem segura (blob -> postgres -> notifica) e,
        #    com `status_controle`, grava o SUCESSO na MESMA transacao das run_* — assim o
        #    estado observavel nunca diverge do dado publicado.
        #    A DDL de resultado NAO roda aqui (criar_schema=False): e migration, feita uma
        #    vez pelo DBA a partir de publicacao.ddl_postgres(tabs).
        notificar = {}
        if service_bus:
            notificar.update({"service_bus": service_bus, "fila": "otimizacoes"})
        if webhook:
            notificar.update({"webhook": webhook, "token": webhook_token})

        payload = PUB.publicar(
            tabs, pg=pg_url, blob=blob, schema=schema_pub, criar_schema=False,
            status_controle=(run_id, schema_ctrl),
            notificar=notificar or None,
            rotulo=f"Unidade {kw.get('unidade')} · foco {kw.get('foco_cobertura')}",
            usuario=p.get("USUARIO", "job-databricks"),
        )
        return {"run_id": run_id, "status": "SUCESSO", "payload": payload}

    except Exception as e:                       # qualquer falha tecnica -> ERRO (nao vazio)
        try:
            PUB.marcar_status_controle(pg_url, run_id, "ERRO", erro=f"{type(e).__name__}: {e}")
        except Exception:                        # banco fora do ar e a causa mais provavel
            print("ATENCAO: falhou tambem ao marcar ERRO:\n" + traceback.format_exc())
        print("ERRO na rodada:\n" + traceback.format_exc())
        raise                                    # `raise` nu preserva o traceback original


# NOTA para revisao:
# - `PUB.gravar_diagnostico` e `PUB.marcar_status_controle` escrevem em
#   controle.run_diagnostico / controle.run_status.
# - A conexao (pg_url) e o service_bus vem de Secret Scope, nunca do codigo. O `blob` e um
#   caminho (config), nao credencial — o acesso ao ADLS vem da configuracao do cluster.
