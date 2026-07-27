"""FASE 3 — Portão de qualidade por rodada.

Roda DEPOIS de otimizar e materializar, ANTES de publicar no Postgres. Se qualquer
checagem falhar, a rodada NAO e publicada: o job marca FALHOU e grava o relatorio.

Este e o portao POR RODADA (qualidade do resultado). O portao de CODIGO (regressao)
e a suite pytest, que roda no CI antes do deploy — nao aqui.

Depende so de `tabs` (as tabelas materializadas), `res` (saida do otimizador) e,
opcionalmente, `cen`. NAO abre conexao nem executa SQL — roda offline e e testavel sem
banco. A unica dependencia externa e a leitura do dicionario `publicacao.CHAVES` (as PKs
das tabelas publicadas), importado sob demanda dentro de `checar` e protegido por
try/except: sem ele a checagem de duplicatas e pulada, o resto continua valendo.

    from producao.qualidade import checar
    ok, relatorio, resumo = checar(cen, res, tabs)
    if not ok:
        # marca FALHOU, grava `relatorio` em run_diagnostico, NAO publica
        ...
"""
from __future__ import annotations
import math

TOL = 0.01   # 1 centavo — muito acima do ruido de ponto flutuante, muito abaixo do relevante

# Sem estas tabelas a rodada nao e publicavel. Como TODAS as checagens abaixo sao
# condicionais a existencia da tabela, um `tabs` degradado passaria com
# "QUALIDADE OK — 2 checagens criticas passaram" — o portao rodaria MENOS checagens em
# vez de reprovar.
TABELAS_OBRIGATORIAS = ("run_meta", "run_obra", "run_subbacia", "run_ano", "run_cidade_ano")


def _isnan(x):
    try:
        return isinstance(x, float) and math.isnan(x)
    except Exception:
        return False


def checar(cen, res, tabs, tol: float = TOL):
    """Roda todas as checagens e devolve (ok, relatorio, resumo).

    - ok: bool — True se TODAS as checagens criticas passaram.
    - relatorio: list[dict] — uma linha por checagem {check, nivel, ok, detalhe}.
    - resumo: str — uma linha legivel para log.
    """
    rel = []

    def add(check, ok, detalhe="", nivel="critico"):
        rel.append({"check": check, "nivel": nivel, "ok": bool(ok), "detalhe": str(detalhe)})

    ro = tabs.get("run_obra"); rs = tabs.get("run_subbacia")
    ra = tabs.get("run_ano"); rm = tabs.get("run_mes")
    rmeta = tabs.get("run_meta"); rdep = tabs.get("run_dependencia")
    rca = tabs.get("run_cidade_ano"); rcob = tabs.get("run_cobertura")
    rmc = tabs.get("run_meta_cobertura")

    # ---- 0. as tabelas obrigatorias existem e nao estao vazias ------------------
    faltando = [t for t in TABELAS_OBRIGATORIAS
                if tabs.get(t) is None or len(tabs[t]) == 0]
    add("Materializacao: tabelas obrigatorias presentes", not faltando,
        f"ausentes/vazias: {faltando}" if faltando else "ok")

    # ---- 0b. run_id unico e igual em TODAS as tabelas --------------------------
    # divergencia aqui viraria violacao de FK DENTRO da transacao de publicacao (ERRO
    # tecnico opaco) em vez de FALHOU_QUALIDADE com o motivo escrito.
    rids = set()
    for nome, df in tabs.items():
        if nome.startswith("snapshot__") or df is None or len(df) == 0:
            continue
        if "run_id" in getattr(df, "columns", []):
            rids |= set(df["run_id"].dropna().unique())
    add("run_id: unico em todas as tabelas", len(rids) == 1,
        f"run_id(s) encontrados: {sorted(rids)}")

    # ---- 0c. sem duplicatas nas chaves primarias -------------------------------
    # barra aqui, e nao no INSERT: duplicata vira erro de constraint no meio da
    # transacao, com mensagem que nao diz de onde veio.
    try:
        from publicacao import CHAVES as _CHAVES
    except Exception:                                    # portao roda offline tambem
        _CHAVES = {}
    dups = []
    for nome, chave in _CHAVES.items():
        df = tabs.get(nome)
        if df is None or not chave or not set(chave) <= set(df.columns):
            continue
        n = int(df.duplicated(subset=list(chave)).sum())
        if n:
            dups.append(f"{nome}({n})")
    add("Chaves: sem duplicatas nas PKs", not dups,
        f"duplicadas em: {dups}" if dups else "ok")

    # ---- 1. status do solver ---------------------------------------------------
    # o cpsat63 devolve "OTIMO", "VIAVEL(limite de tempo)" — sempre com sufixos, ex.
    # "OTIMO | OBRIG 3/3", "OTIMO | lexicografico: ..." — ou "SEM SOLUCAO(<st>)".
    # A comparacao anterior era `st in ("OPTIMAL","FEASIBLE")`, que NUNCA e verdadeira:
    # o portao reprovava 100% das rodadas bem-sucedidas e nada seria publicado.
    st = str(res.get("milp_status", "")).upper()
    add("Status do solver", st.startswith(("OTIMO", "VIAVEL", "OPTIMAL", "FEASIBLE")),
        f"status={st or '?'} (esperado OTIMO/VIAVEL)")

    # ---- 2. reconciliacoes (tem de fechar em ~zero) ----------------------------
    if rs is not None:
        d = float(rs["vpl"].sum()) - float(res["vpl"])
        add("VPL: soma por sub-bacia = VPL do plano", abs(d) <= tol, f"diferenca R$ {d:,.4f}")
    if ra is not None and rmeta is not None:
        d = float(ra["capex"].sum()) - float(rmeta["capex_total"].iloc[0])
        add("CAPEX: run_ano = run_meta", abs(d) <= tol, f"diferenca R$ {d:,.4f}")
    if ra is not None and rm is not None:
        d = float(rm["capex_mes"].sum()) - float(ra["capex"].sum())
        add("CAPEX: run_mes = run_ano", abs(d) <= tol, f"diferenca R$ {d:,.4f}")
    if ra is not None and rca is not None:
        d = float(rca["capex"].sum()) - float(ra["capex"].sum())
        add("CAPEX: run_cidade_ano = run_ano", abs(d) <= tol, f"diferenca R$ {d:,.4f}")

    # ---- 3. rateio por vazao: fracoes somam 1 em cada obra compartilhada --------
    if rdep is not None and len(rdep):
        f = rdep.groupby("obra_id").fracao_rateio.sum()
        dev = float((f - 1.0).abs().max()) if len(f) else 0.0
        add("Rateio: fracoes somam 1 por obra", dev < 1e-6, f"desvio maximo {dev:.2e}")

    # ---- 4. teto de orcamento respeitado (dentro da janela) --------------------
    if ra is not None and "excesso" in ra.columns:
        anos_estouro = int((ra["excesso"] > 1).sum())
        add("Orcamento: teto anual respeitado", anos_estouro == 0,
            f"{anos_estouro} ano(s) com estouro de teto")

    # ---- 4b. o teto EXISTE ------------------------------------------------------
    # sem orcamento (parametro nem aba) o motor usa INF, e a checagem acima passa
    # trivialmente: plano irrestrito publicado como SUCESSO.
    if ra is not None and "teto_capex" in ra.columns and len(ra):
        sem_teto = int((~ra["teto_capex"].between(0, 1e17)).sum())
        add("Orcamento: teto definido em todos os anos", sem_teto == 0,
            f"{sem_teto} ano(s) sem teto (CAPEX ilimitado)")

    # ---- 5. sem NaN em colunas-chave -------------------------------------------
    faltas = []
    for nome, df, cols in (
        ("run_obra", ro, ["obra_id", "tipo", "capex", "construida"]),
        ("run_subbacia", rs, ["sub_bacia", "vpl", "faturando"]),
        ("run_ano", ra, ["capex", "opex", "receita"]),
    ):
        if df is None:
            continue
        for c in cols:
            if c in df.columns and df[c].isna().any():
                faltas.append(f"{nome}.{c}")
    add("Integridade: colunas-chave sem NaN", not faltas, f"NaN em: {faltas}" if faltas else "ok")

    # ---- 6. metas de cobertura: deficit coerente (>= 0) ------------------------
    # a coluna chama-se `deficit_ligacoes` (persistencia.py). Enquanto isto procurava
    # "deficit", a checagem era condicional a uma coluna inexistente: nunca rodava, e
    # ninguem percebia — o relatorio simplesmente vinha com uma checagem a menos.
    if rmc is not None and "deficit_ligacoes" in rmc.columns and len(rmc):
        neg = int((rmc["deficit_ligacoes"] < -tol).sum())
        add("Metas: deficit nao-negativo", neg == 0, f"{neg} meta(s) com deficit negativo",
            nivel="critico")

    # ---- 7. cobertura sana (nao-negativa) --------------------------------------
    # idem: a coluna e `cobertura_pct`, nao `cobertura`.
    if rcob is not None and "cobertura_pct" in rcob.columns and len(rcob):
        neg = int((rcob["cobertura_pct"] < -tol).sum())
        add("Cobertura: valores nao-negativos", neg == 0, f"{neg} linha(s) negativa(s)")

    # ---- 8. plano nao-vazio (aviso, nao bloqueia) ------------------------------
    if ro is not None:
        n_constr = int(ro["construida"].sum()) if "construida" in ro.columns else 0
        add("Plano nao-vazio", n_constr > 0, f"{n_constr} obra(s) construida(s)",
            nivel="aviso")

    criticos = [r for r in rel if r["nivel"] == "critico"]
    ok = all(r["ok"] for r in criticos)
    n_falhas = sum(1 for r in criticos if not r["ok"])
    resumo = ("QUALIDADE OK — %d checagens criticas passaram" % len(criticos)) if ok \
        else ("QUALIDADE FALHOU — %d de %d checagens criticas falharam" % (n_falhas, len(criticos)))
    return ok, rel, resumo


def imprimir(relatorio, resumo):
    """Log legivel do relatorio (para o driver do job)."""
    print("=" * 70)
    for r in relatorio:
        marca = "OK  " if r["ok"] else ("FALHA" if r["nivel"] == "critico" else "aviso")
        print(f"  [{marca:<5}] {r['check']:<45} {r['detalhe']}")
    print("-" * 70)
    print("  " + resumo)
    print("=" * 70)
