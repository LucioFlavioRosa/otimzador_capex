# =============================================================================
#  LEITOR DE RESULTADOS — reconstroi as visualizacoes SO A PARTIR DAS TABELAS
#
#  Este modulo NAO importa o engine, nem o solver, nem o dashboard. Ele so le
#  DataFrames. Se tudo aqui funciona, o contrato de dados esta completo: o
#  backend em producao consegue montar as mesmas telas lendo o Postgres.
#
#      import leitor as L
#      T = L.carregar('resultados_otimizador/')      # ou L.carregar(pasta, run_id)
#      L.kpis(T); L.painel_geral(T); L.explicar(T, 'b90_1_1')
#      L.topologia_sistema(T, 'Sistema 27') # o SISTEMA inteiro, com todos os componentes
#
#  Funciona tanto com as tabelas lidas do disco quanto com o dict que
#  persistencia.materializar() devolve — nao precisa salvar antes.
# =============================================================================
import os as _os

import matplotlib.pyplot as plt
import pandas as pd

TEAL = "#0D9488"; INK = "#0F2E2B"; ORANGE = "#B45309"; RED = "#B91C1C"
CTS_COR = "#0369A1"   # sky-700: cor propria dos nos de CTS (azul, distinta do teal da sub-bacia e do lilas da ETE)
BLUE = "#1C7293"; GREY = "#94A3B8"; GREEN = "#15803D"; LILAC = "#7C3AED"
NOME_ELEM = {"lig": "Ligacao", "rede": "Rede", "tro": "Tronco", "eee": "EEE",
             "lr": "Linha de recalque", "cts": "Coletor de tempo seco",
             "ete": "ETE", "ete_mod": "ETE (modulo)"}
def _nome_elemento(obra_id, tipo):
    """Nome PRECISO do elemento (Transporte nunca agrupado)."""
    return NOME_ELEM.get(_componente_de(obra_id, tipo), NOME_TIPO.get(tipo, tipo))
NOME_TIPO = {"coleta": "Ligacao", "rede": "Rede", "transporte": "Transporte",
             "ete": "ETE", "ete_mod": "ETE (modulo)"}


def _brl(v, casas=0):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    return f"R$ {v:,.{casas}f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _unit_txt(r, curto=False):
    """'2.472,6 m x R$ 449,99/m' a partir de uma linha de run_obra."""
    q = r.get("quantidade"); pu = r.get("preco_unitario")
    if q is None or pu is None or pd.isna(q) or pd.isna(pu):
        return ""
    u = r.get("unidade") or "un"
    if curto:
        return f"{q:,.0f} {u}"
    return f"{q:,.1f} {u} x {_brl(pu, 2)}/{u}"


# ------------------------------------------------------------------- carga
def listar_runs(pasta):
    """Rodadas disponiveis na pasta, a partir de run_meta."""
    T = carregar(pasta, tabelas=["run_meta"])
    m = T.get("run_meta")
    if m is None or m.empty:
        print("nenhuma rodada encontrada em " + str(pasta))
        return None
    cols = [c for c in ["run_id", "data_hora", "rotulo", "anos_capex", "vpl", "capex_total",
                        "obras_construidas", "subbacias_faturando", "metas_nao_atingidas",
                        "milp_status"] if c in m.columns]
    return m[cols].sort_values("data_hora", ascending=False)


def carregar(pasta, run_id=None, tabelas=None):
    """Le a estrutura gravada por persistencia.salvar(): pasta/<tabela>/run_id=<id>/dados.*"""
    base = str(pasta).rstrip("/")
    if not _os.path.isdir(base):
        raise FileNotFoundError(base)
    out = {}
    for nome in sorted(_os.listdir(base)):
        if tabelas and nome not in tabelas:
            continue
        p = _os.path.join(base, nome)
        if not _os.path.isdir(p):
            continue
        partes = []
        for sub in sorted(_os.listdir(p)):
            if run_id and sub != f"run_id={run_id}":
                continue
            d = _os.path.join(p, sub)
            arqs = ([_os.path.join(d, a) for a in sorted(_os.listdir(d))]
                    if _os.path.isdir(d) else [d])
            for a in arqs:
                if a.endswith(".parquet"):
                    partes.append(pd.read_parquet(a))
                elif a.endswith(".csv"):
                    partes.append(pd.read_csv(a))
        if partes:
            out[nome] = pd.concat(partes, ignore_index=True)
    if not run_id and "run_meta" in out and len(out["run_meta"]) > 1:
        ult = out["run_meta"].sort_values("data_hora").run_id.iloc[-1]
        print(f"[info] {len(out['run_meta'])} rodadas na pasta — usando a mais recente: {ult}")
        out = {k: (v[v.run_id == ult] if "run_id" in v.columns else v) for k, v in out.items()}
    return out


def _ab(T):
    m = T["run_meta"].iloc[0]
    return int(m["ano_base"]) if "ano_base" in m else int(T["run_ano"].ano.min())


# --------------------------------------------------------------------- KPIs
def _ebitda_total(T):
    an = T.get("run_ano")
    return float(an["ebitda"].sum()) if (an is not None and "ebitda" in an.columns) else None


def _ebitda_vira(T):
    an = T.get("run_ano")
    if an is None or "ebitda" not in an.columns:
        return "-"
    pos = an[an["ebitda"] > 0].sort_values("ano")
    return str(int(pos.iloc[0]["ano"])) if len(pos) else "nunca no horizonte"


def ebitda(T, cidade=None, salvar=None):
    """EBITDA de curto prazo (saida calculada, fora da funcao objetivo).
    EBITDA = receita operacional (ligacoes novas + efeito-base da paridade) - OPEX, ano a ano.
    cidade=None -> unidade inteira, eixo ate o fim da janela de CAPEX + 3 anos.
    cidade='Nome' -> so aquela cidade, eixo ate o FIM DA CONCESSAO da cidade."""
    import matplotlib.patches as _mp
    from matplotlib.lines import Line2D as _L2
    m = T["run_meta"].iloc[0]
    ab = _ab(T); acap = int(m["anos_capex"]) if "anos_capex" in m else None

    if cidade is not None:
        sa = T.get("run_subbacia_ano")
        if sa is None or "ebitda" not in sa.columns:
            print("run_subbacia_ano nao tem EBITDA por cidade — regrave com persistencia atual.")
            return None
        d = sa[sa.cidade == cidade]
        if d.empty:
            print(f"cidade '{cidade}' sem serie. Opcoes: {sorted(sa.cidade.unique())[:12]}")
            return None
        an = d.groupby("ano").agg(receita_direta=("receita_direta", "sum"),
                                  receita_indireta=("receita_indireta", "sum"),
                                  efeito_base=("efeito_base", "sum"),
                                  opex=("opex_rateado", "sum"),
                                  ebitda=("ebitda", "sum")).reset_index().sort_values("ano")
        an["receita_total"] = an.receita_direta + an.receita_indireta + an.efeito_base
        an["ebitda_margem_pct"] = an.apply(
            lambda r: (r.ebitda / r.receita_total * 100.0) if r.receita_total else None, axis=1)
        titulo = f"EBITDA por ano — {cidade}  (receita operacional - OPEX)"
        # fim da concessao da cidade (run_sistema) ou o ultimo ano com dado
        si = T.get("run_sistema")
        if si is not None and "ano_fim_concessao" in si.columns and (si.cidade == cidade).any():
            fim_x = int(si[si.cidade == cidade].ano_fim_concessao.max())
        else:
            fim_x = int(an.ano.max())
    else:
        an = T.get("run_ano")
        if an is None or "ebitda" not in an.columns:
            print("run_ano nao tem EBITDA — regrave com persistencia atual.")
            return None
        an = an.sort_values("ano")
        titulo = "EBITDA por ano  (receita operacional - OPEX)"
        fim_x = (ab + acap - 1 + 3) if acap else int(an.ano.max())   # unidade: fim do CAPEX + 3

    # recorta a serie ate o fim do eixo (evita cauda vazia)
    an = an[an.ano <= fim_x]
    tot = float(an["ebitda"].sum())
    tem_neg = bool((an["ebitda"] < 0).any())
    vira = an[an["ebitda"] > 0].sort_values("ano")

    fig, a = plt.subplots(figsize=(11, 5.6))
    cor = [TEAL if v >= 0 else RED for v in an["ebitda"]]
    a.bar(an.ano, an["ebitda"] / 1e6, color=cor, edgecolor="white")
    a.axhline(0, color=GREY, lw=1)
    if len(vira):
        y0 = int(vira.iloc[0]["ano"])
        nota = f"EBITDA > 0\nem {y0}"
        if tem_neg:
            nota = f"negativo ate {y0-1},\npositivo a partir de {y0}"
        a.annotate(nota, (y0, 0), textcoords="offset points", xytext=(6, 22),
                   fontsize=8.5, color=GREEN, weight="bold")
    a.set_ylabel("EBITDA (R$ milhoes/ano)"); a.grid(alpha=.2, axis="y")
    a2 = a.twinx()
    a2.plot(an.ano, an["ebitda_margem_pct"], color=INK, lw=1.8, marker="o", ms=3)
    a2.set_ylabel("margem EBITDA (%)", color=INK)
    _mg = an["ebitda_margem_pct"].dropna()
    if len(_mg):
        lo = min(-10, float(_mg.min()) * 1.1); hi = max(110, float(_mg.max()) * 1.1)
        a2.set_ylim(lo, min(hi, 200))
    if acap:
        a.axvline(ab + acap - 1 + 0.5, color=ORANGE, ls="--", lw=1.3)
        a.text(ab + acap - 1 + 0.5, a.get_ylim()[1] * 0.96, " fim do CAPEX",
               fontsize=8, color=ORANGE, ha="left", va="top")
    a.set_xlim(ab - 0.7, fim_x + 0.7)
    a.set_title(f"{titulo}   ·   EBITDA total {_brl(tot)}", weight="bold", fontsize=12)
    # legenda: o que e barra, o que e linha
    leg = [_mp.Patch(facecolor=TEAL, edgecolor="white", label="EBITDA positivo (barra)"),
           _mp.Patch(facecolor=RED, edgecolor="white", label="EBITDA negativo (barra)"),
           _L2([0], [0], color=INK, lw=1.8, marker="o", ms=3, label="margem EBITDA % (linha preta)")]
    a.legend(handles=leg, fontsize=8.5, loc="lower right", framealpha=.9)
    plt.tight_layout()
    if salvar:
        fig.savefig(salvar, dpi=120)
    return fig


def kpis(T, imprimir=True):
    m = T["run_meta"].iloc[0]
    ob = T["run_obra"]
    linhas = [
        ("VPL do plano", _brl(m["vpl"])),
        ("CAPEX total", _brl(m["capex_total"])),
        ("OPEX total", _brl(m["opex_total"])),
        ("Receita total", _brl(m["receita_total"])),
        ("Obras construidas", f"{int(m['obras_construidas'])} de {int(m['obras_total'])}"),
        ("Obrigatorias", f"{m.get('obrig_construidas')}/{m.get('obrig_total')}"),
        ("Sub-bacias faturando", f"{int(m['subbacias_faturando'])} de {int(m['subbacias_total'])}"),
        ("Cobertura no fim", f"{m.get('cobertura_final_pct'):.1f}%"
            if pd.notna(m.get("cobertura_final_pct")) else "-"),
        ("Metas nao atingidas", f"{m.get('metas_nao_atingidas')} de {m.get('metas_total')}"),
        ("VP do efeito-base", _brl(m["vp_efeito_base"])),
        ("EBITDA total (nominal)", _brl(_ebitda_total(T))),
        ("EBITDA vira positivo em", _ebitda_vira(T)),
        ("Teto anual respeitado", "SIM" if m.get("auditoria_ok", True) else "NAO"),
        ("Status do solver", str(m.get("milp_status"))),
    ]
    df = pd.DataFrame(linhas, columns=["Indicador", "Valor"]).set_index("Indicador")
    if imprimir:
        print(f"=== {m.get('rotulo') or m['run_id']} ===")
        print(df.to_string())
        print(f"\nobras por status: {ob.status.value_counts().to_dict()}")
    return df


# ------------------------------------------------------------- painel geral
def painel_geral(T, salvar=None):
    an = T["run_ano"].sort_values("ano")
    sb = T["run_subbacia"]; ob = T["run_obra"]
    cb = T["run_cobertura"]; mc = T["run_meta_cobertura"]
    m = T["run_meta"].iloc[0]
    yrs = an.ano.tolist()
    fig, ax = plt.subplots(3, 2, figsize=(15, 13))
    fig.suptitle(f"{m.get('rotulo') or m['run_id']}   |   VPL {_brl(m['vpl'])}   "
                 f"|   reconstruido SO das tabelas", fontsize=14, weight="bold", y=0.997)

    a = ax[0, 0]
    a.bar(yrs, an.capex / 1e6, color=TEAL, label="CAPEX")
    a.bar(yrs, an.opex / 1e6, bottom=an.capex / 1e6, color=ORANGE, label="OPEX")
    a.plot(yrs, an.receita / 1e6, color=INK, lw=2.2, marker="o", ms=3, label="Receita")
    a.plot(yrs, an.teto_capex / 1e6, color=RED, ls="--", lw=1.4, label="teto de CAPEX")
    a.set_ylabel("R$ milhoes/ano"); a.grid(alpha=.2); a.legend(fontsize=8)
    a.set_title("Desembolso e receita por ano  (run_ano)", weight="bold")

    a = ax[0, 1]
    ms_ = T.get("run_mes")
    if ms_ is not None and len(ms_):
        ms_ = ms_.sort_values("mes_indice")
        a.plot(ms_.mes_indice / 12 + _ab(T), ms_.capex_acumulado / 1e6, color=TEAL, lw=2.4)
        a.fill_between(ms_.mes_indice / 12 + _ab(T), ms_.capex_acumulado / 1e6,
                       color=TEAL, alpha=.15)
        a.set_ylabel("R$ milhoes"); a.set_title("Curva S — CAPEX acumulado  (run_mes)", weight="bold")
    a.grid(alpha=.2)

    a = ax[1, 0]
    passos = [("Receita\ndireta", sb.vp_receita_direta.sum()),
              ("Receita\nindireta", sb.vp_receita_indireta.sum()),
              ("Efeito-base\nparidade", sb.vp_efeito_base.sum()),
              ("CAPEX", sb.vp_capex_rateado.sum()), ("OPEX", sb.vp_opex_rateado.sum())]
    base = 0.0
    for i, (lab, v) in enumerate(passos):
        a.bar(i, v / 1e6, bottom=base / 1e6, color=(TEAL if v >= 0 else RED))
        a.text(i, (base + v / 2) / 1e6, f"{v/1e6:+,.0f}", ha="center", va="center",
               fontsize=8, color="white", weight="bold")
        base += v
    a.bar(len(passos), base / 1e6, color=INK)
    a.text(len(passos), base / 2e6, f"{base/1e6:,.0f}", ha="center", va="center",
           fontsize=9, color="white", weight="bold")
    a.set_xticks(range(len(passos) + 1)); a.set_xticklabels([p[0] for p in passos] + ["VPL"], fontsize=8)
    a.axhline(0, color=GREY, lw=1); a.grid(alpha=.2, axis="y")
    a.set_ylabel("R$ milhoes (VP)")
    a.set_title("Cascata do VPL  (run_subbacia)", weight="bold")

    a = ax[1, 1]
    # CAPEX por ELEMENTO: Transporte quebrado em Tronco / EEE / Linha de recalque
    _cc = ob[ob.construida].copy()
    if "componente" not in _cc.columns:
        _cc["componente"] = [_componente_de(i, t) for i, t in zip(_cc.obra_id, _cc.tipo)]
    _COMP_NOME = {"lig": "Ligacao", "rede": "Rede", "tro": "Tronco",
                  "eee": "EEE", "lr": "Linha de recalque"}
    def _grupo_capex(r):
        if r["tipo"] in ("ete", "ete_mod"):
            return NOME_TIPO.get(r["tipo"], r["tipo"])
        return _COMP_NOME.get(str(r["componente"]), NOME_TIPO.get(r["tipo"], str(r["tipo"])))
    _cc["grupo"] = _cc.apply(_grupo_capex, axis=1)
    ct = (_cc.groupby("grupo").capex.sum().sort_values() / 1e6)
    # cor por elemento (mesma paleta do grafico de obras por ano)
    _CORG = {"Ligacao": TEAL, "Rede": BLUE, "Tronco": ORANGE, "EEE": "#0EA5E9",
             "Linha de recalque": "#A16207", "ETE (modulo)": LILAC, "ETE": LILAC}
    a.barh(ct.index, ct.values, color=[_CORG.get(g, BLUE) for g in ct.index])
    for i, v in enumerate(ct.values):
        a.text(v, i, f"  {v:,.0f}", va="center", fontsize=9)
    a.set_xlabel("R$ milhoes"); a.grid(alpha=.2, axis="x"); a.margins(x=.18)
    a.set_title("CAPEX por elemento de obra  (run_obra)", weight="bold")

    # ---- histograma do VPL por sub-bacia, colorido por sinal ----
    a = ax[2, 0]
    vpl = (sb.vpl / 1e6).dropna()
    npos = int((vpl > 0).sum()); nneg = int((vpl <= 0).sum())
    import numpy as _np
    lo, hi = vpl.min(), vpl.max()
    bins = _np.linspace(lo, hi, 31) if hi > lo else 10
    cont, borda, _ = a.hist(vpl, bins=bins, edgecolor="white")
    for ret, esq in zip(_, borda[:-1]):
        ret.set_facecolor(TEAL if esq >= 0 else RED)
    a.axvline(0, color=INK, lw=1.4, ls="--")
    a.set_xlabel("VPL da sub-bacia (R$ milhoes)"); a.set_ylabel("nº de sub-bacias")
    a.grid(alpha=.2, axis="y")
    a.set_title(f"Quantidade de sub-bacias por VPL  "
                f"({npos} positivas, {nneg} negativas)  (run_subbacia)", weight="bold")

    # ---- quantidade de obras por ano, empilhada por tipo de elemento (+ ETE) ----
    a = ax[2, 1]
    con = ob[ob.construida].copy()
    con["ano_inicio"] = con.data_inicio.astype(str).str.slice(0, 4)
    con = con[con.ano_inicio.str.len() == 4]
    # tipo do elemento: componente (lig/rede/tro/eee/lr) ou ETE (ete_mod)
    if "componente" not in con.columns:
        con["componente"] = [_componente_de(i, t) for i, t in zip(con.obra_id, con.tipo)]
    con["grupo"] = con.apply(
        lambda r: "ETE" if r["tipo"] in ("ete", "ete_mod") else str(r["componente"]), axis=1)
    ORDEM = [("lig", "Ligacao", TEAL), ("rede", "Rede", BLUE), ("tro", "Tronco", ORANGE),
             ("eee", "EEE", "#0EA5E9"), ("lr", "Linha de recalque", "#A16207"),
             ("ETE", "ETE", LILAC)]
    if len(con):
        anos_i = sorted(int(x) for x in con.ano_inicio.unique())
        base = {y: 0 for y in anos_i}
        for cod, rot, cor in ORDEM:
            vals = [int(((con.grupo == cod) & (con.ano_inicio == str(y))).sum()) for y in anos_i]
            if sum(vals) == 0:
                continue
            a.bar(anos_i, vals, bottom=[base[y] for y in anos_i], color=cor,
                  edgecolor="white", label=f"{rot} ({sum(vals)})")
            for k, y in enumerate(anos_i):
                base[y] += vals[k]
        for y in anos_i:
            a.text(y, base[y], str(base[y]), ha="center", va="bottom", fontsize=8, color=INK)
        a.set_xlabel("ano de inicio da obra"); a.set_ylabel("nº de obras")
        a.set_title(f"Quantidade de obras por ano  ({len(con)} obras)  (run_obra)", weight="bold")
        a.legend(fontsize=7.5, ncol=2, loc="upper right")
        # eixo so ate o fim da janela de CAPEX (obra nao inicia depois disso)
        ab = _ab(T); acap = int(m["anos_capex"]) if "anos_capex" in m else None
        if acap:
            a.set_xlim(ab - 0.7, ab + acap - 1 + 0.7)
        a.xaxis.set_major_locator(__import__("matplotlib").ticker.MaxNLocator(integer=True))
    a.grid(alpha=.2, axis="y")
    plt.tight_layout(rect=[0, 0, 1, 0.985])
    if salvar:
        fig.savefig(salvar, dpi=120)
    return fig


# ----------------------------------------------------- obras: entrou / saiu
def obras_fora(T, cidade=None, categoria=None, n=25):
    ob = T["run_obra"]
    d = ob[ob.status == "FORA"]
    if cidade:
        d = d[d.cidade == cidade]
    if categoria:
        d = d[d.categoria_motivo == categoria]
    cols = [c for c in ["obra_id", "tipo", "cidade", "no", "categoria_motivo",
                        "elo_que_trava", "capex", "ligacoes", "saldo_potencial", "motivo"]
            if c in d.columns]
    print(f"{len(d)} obra(s) fora do plano  |  CAPEX envolvido: {_brl(d.capex.sum())}")
    print("\nPOR CATEGORIA:")
    for c, k in d.categoria_motivo.value_counts().items():
        print(f"  {k:5}x  {c:<44}{_brl(d[d.categoria_motivo == c].capex.sum()):>18}")
    return d[cols].sort_values("capex", ascending=False).head(n) if n else d[cols]


def explicar(T, sub_bacia):
    """Narrativa de por que a sub-bacia nao entrou — montada SO com colunas persistidas."""
    sb = T["run_subbacia"]
    linha = sb[sb.sub_bacia == sub_bacia]
    if linha.empty:
        print(f"sub-bacia '{sub_bacia}' nao encontrada.")
        return
    r = linha.iloc[0]
    ob = T["run_obra"]
    coleta = ob[ob.obra_id == r["obra_coleta"]] if pd.notna(r.get("obra_coleta")) else None
    print("=" * 78)
    print(f"{sub_bacia}   ({r['cidade']} / {r['sistema']})")
    print("=" * 78)
    if r["faturando"]:
        print(f"  FATURA a partir de {r['data_inicio_faturamento']}.")
        print(f"  VPL: {_brl(r['vpl'])}   (receita {_brl(r['vp_receita_direta'] + r['vp_receita_indireta'])}"
              f" · CAPEX {_brl(r['vp_capex_rateado'])} · OPEX {_brl(r['vp_opex_rateado'])}"
              f" · efeito-base {_brl(r['vp_efeito_base'])})")
        print("=" * 78)
        return
    if coleta is not None and not coleta.empty:
        c = coleta.iloc[0]
        print(f"  CATEGORIA: {c['categoria_motivo']}")
        if pd.notna(c.get("elo_que_trava")) and str(c["elo_que_trava"]).strip():
            e = ob[ob.obra_id == c["elo_que_trava"]]
            cap = _brl(e.iloc[0]["capex"]) if not e.empty else "?"
            print(f"  ELO QUE TRAVA: {c['elo_que_trava']} ({cap})")
        print()
        for i in range(0, len(str(c["motivo"])), 74):
            print("  " + str(c["motivo"])[i:i + 74])
    print("\n  SE FOSSE LIGADA AGORA (valor presente):")
    print(f"    {'(+) Receita':<32}{_brl(r.get('pot_vp_receita')):>18}")
    print(f"    {'(-) CAPEX a construir, sozinha':<32}{_brl(r.get('pot_vp_capex_solo')):>18}")
    print(f"    {'(-) OPEX':<32}{_brl(r.get('pot_vp_opex')):>18}")
    print(f"    {'-' * 50}")
    print(f"    {'SALDO sozinha':<32}{_brl(r.get('pot_saldo_solo')):>18}")
    print(f"    {'SALDO com rateio por vazao':<32}{_brl(r.get('pot_saldo_rateado')):>18}")
    print("=" * 78)


# ----------------------------------------------------- navegacao por NOME
def _valida(nome, opcoes, rotulo):
    """Aceita so nomes existentes; se errar, mostra as opcoes. Nunca indice."""
    opcoes = list(opcoes)
    if nome is None:
        return opcoes[0] if opcoes else None
    if nome in opcoes:
        return nome
    prox = [o for o in opcoes if str(nome).lower() in str(o).lower()]
    print(f"{rotulo} '{nome}' nao existe.")
    print(f"  {'parecidos: ' + ', '.join(map(str, prox[:8])) if prox else 'opcoes: ' + ', '.join(map(str, opcoes[:12]))}")
    return None


def cidades(T):
    """NIVEL 2 — uma linha por cidade, ordenada por CAPEX. Ponto de entrada do drill-down."""
    cd = T["run_cidade"].copy()
    cols = [c for c in ["cidade", "sub_bacias", "obras_feitas", "obras_fora", "capex_total",
                        "vpl", "ligacoes_novas", "cobertura_base_pct", "cobertura_final_pct",
                        "metas_total", "metas_atingidas", "paridade_inicial", "paridade_final"]
            if c in cd.columns]
    return cd[cols].sort_values("capex_total", ascending=False).reset_index(drop=True)


def sistemas(T, cidade=None):
    """NIVEL 3 — sistemas de uma cidade (ou de todas), com capacidade e ocupacao."""
    d = listar_sistemas(T)
    if cidade is not None:
        cidade = _valida(cidade, sorted(T["run_cidade"].cidade), "cidade")
        if cidade is None:
            return None
        d = d[d.cidade == cidade]
    return d.sort_values("sub_bacias", ascending=False).reset_index(drop=True)


def subbacias(T, sistema=None, cidade=None):
    """NIVEL 4 — sub-bacias de um sistema (ou de uma cidade), com situacao e VPL."""
    sb = T["run_subbacia"].copy()
    if sistema is not None:
        sistema = _valida(sistema, sorted(sb.sistema.unique()), "sistema")
        if sistema is None:
            return None
        sb = sb[sb.sistema == sistema]
    if cidade is not None:
        sb = sb[sb.cidade == cidade]
    cols = [c for c in ["sub_bacia", "tipo_estrutura", "cidade", "sistema", "jusante", "faturando",
                        "ligacoes_novas", "vazao_marginal", "vpl", "pot_saldo_rateado",
                        "data_inicio_faturamento"] if c in sb.columns]
    return sb[cols].sort_values(["faturando", "vpl"], ascending=[False, False]).reset_index(drop=True)


def elementos(T, sub_bacia=None, sistema=None):
    """NIVEL 5 — elementos (obras) de uma sub-bacia, na ordem dos componentes.
    Inclui os que NAO tem obra prevista, com CAPEX 0."""
    ob = T["run_obra"].copy()
    if "componente" not in ob.columns:
        ob["componente"] = [_componente_de(i, t) for i, t in zip(ob.obra_id, ob.tipo)]
    if sub_bacia is not None:
        sub_bacia = _valida(sub_bacia, sorted(T["run_subbacia"].sub_bacia), "sub-bacia")
        if sub_bacia is None:
            return None
        d = ob[ob.no == sub_bacia]
        _ehcts = bool(len(d) and d.get("is_cts", pd.Series([False])).any())
        _comps = COMPONENTES_CTS if _ehcts else COMPONENTES
        rot = dict(COMPONENTES) | dict(COMPONENTES_CTS)
        presentes = set(d.componente)
        faltando = [{"obra_id": f"(sem obra) {rot[c]}", "componente": c, "capex": 0.0,
                     "responsavel": "-", "construida": False, "status": "SEM OBRA PREVISTA",
                     "no": sub_bacia} for c, _ in _comps if c not in presentes]
        if faltando:
            d = pd.concat([d, pd.DataFrame(faltando)], ignore_index=True)
        ordem = {c: i for i, (c, _) in enumerate(_comps)}
        d = d.assign(_o=d.componente.map(lambda c: ordem.get(c, 99))).sort_values("_o").drop(columns="_o")
    elif sistema is not None:
        ids = set(T["run_subbacia"].query("sistema == @sistema").sub_bacia)
        d = ob[(ob.no.isin(ids)) | (ob.sistema == sistema)]
    else:
        d = ob
    cols = [c for c in ["obra_id", "componente", "tipo", "no", "cidade",
                        "quantidade", "unidade", "preco_unitario", "capex", "opex_ano",
                        "prazo_meses", "responsavel", "construida", "data_inicio", "data_pronta",
                        "status", "categoria_motivo"] if c in d.columns]
    return d[cols].reset_index(drop=True)


def elemento(T, obra_id):
    """NIVEL 6 — o ultimo nivel: tudo sobre UM elemento e quem depende dele."""
    ob = T["run_obra"]
    obra_id = _valida(obra_id, sorted(ob.obra_id), "elemento")
    if obra_id is None:
        return None
    o = ob[ob.obra_id == obra_id].iloc[0]
    _cts = bool(o.get("is_cts"))
    print("=" * 78)
    print(f"ELEMENTO {obra_id}   [{_nome_elemento(obra_id, o['tipo'])}]"
          + ("  (CTS)" if _cts else "") + f"   ->   {o['status']}")
    print("=" * 78)
    print(f"  cidade / sistema     : {o['cidade']} / {o.get('sistema') or '-'}")
    print(f"  {'CTS (no)            ' if _cts else 'sub-bacia (no)      '} : {o.get('no') or '-'}")
    print(f"  responsavel          : {o['responsavel']}"
          + ("   (OBRIGATORIA)" if o.get("obrigatoria") else ""))
    _cu = _unit_txt(o)
    if _cu:
        print(f"  quantidade x preco   : {_cu}")
    print(f"  CAPEX                : {_brl(o['capex'])}"
          + (f"   ({o.get('quantidade'):,.1f} {o.get('unidade') or 'un'}"
             f" x {_brl(o.get('preco_unitario'), 2)})" if _cu else ""))
    if pd.notna(o.get("capex_componentes")):
        print(f"     componentes       : {o['capex_componentes']}")
    print(f"  OPEX/ano             : {_brl(o.get('opex_ano'))}")
    print(f"  prazo de execucao    : {o.get('prazo_meses')} meses"
          f"   |   inicio mais cedo: mes {o.get('inicio_min_mes')}")
    if pd.notna(o.get("wacc")):
        _wo = o.get("wacc_origem")
        _wt = (" · médio da unidade" if _wo == "wacc_medio"
               else (" · próprio (financiamento contratado)" if _wo == "proprio" else ""))
        print(f"  WACC do elemento     : {o['wacc']:.2%}{_wt}")
    if o["tipo"] == "coleta":
        print(f"  ligacoes novas       : {o.get('ligacoes'):,.0f}"
              f"   |   ticket {_brl(o.get('ticket_mes'), 2)}/mes"
              f"   |   preco/ligacao {_brl(o.get('preco_ligacao'), 2)}")
    print()
    print(f"  DECISAO              : {'CONSTRUIDA' if o['construida'] else 'FORA DO PLANO'}")
    print(f"    inicio             : {o.get('data_inicio') or '-'}")
    print(f"    pronta             : {o.get('data_pronta') or '-'}")
    if pd.notna(o.get("data_inicio_faturamento")):
        print(f"    fatura a partir de : {o['data_inicio_faturamento']}")
    if pd.notna(o.get("categoria_motivo")):
        print(f"    categoria          : {o['categoria_motivo']}")
    if pd.notna(o.get("elo_que_trava")) and str(o["elo_que_trava"]).strip():
        print(f"    elo que trava      : {o['elo_que_trava']}")
    if pd.notna(o.get("motivo")):
        print("\n  POR QUE:")
        m = str(o["motivo"])
        for i in range(0, len(m), 72):
            print("    " + m[i:i + 72])
    dp = T.get("run_dependencia")
    if dp is not None:
        d = dp[dp.obra_id == obra_id]
        if len(d):
            print(f"\n  QUEM DEPENDE DESTE ELEMENTO ({len(d)} sub-bacia(s)):")
            print(f"    {'sub-bacia':<16}{'vazao':>10}{'fatia':>9}{'CAPEX rateado':>18}{'fatura?':>10}")
            for _, r in d.sort_values("fracao_rateio", ascending=False).head(12).iterrows():
                print(f"    {r['sub_bacia']:<16}{r['vazao_sub_bacia']:>10,.1f}"
                      f"{r['fracao_rateio']:>8.1%}{_brl(r['capex_rateado']):>18}"
                      f"{('SIM' if r['sub_bacia_faturando'] else 'nao'):>10}")
            if len(d) > 12:
                print(f"    ... e mais {len(d)-12}")
    print("=" * 78)
    return ob[ob.obra_id == obra_id]


# --------------------------------------------------------------- sub-bacia
def deep_dive(T, sub_bacia, salvar=None):
    """Cascata do VPL + curva de receita da sub-bacia, so das tabelas."""
    sbT = T["run_subbacia"]; sa = T.get("run_subbacia_ano")
    linha = sbT[sbT.sub_bacia == sub_bacia]
    if linha.empty:
        print(f"sub-bacia '{sub_bacia}' nao encontrada.")
        return
    r = linha.iloc[0]
    itens = [("Rec.\ndireta", r["vp_receita_direta"]), ("Rec.\nindireta", r["vp_receita_indireta"]),
             ("Efeito\nbase", r["vp_efeito_base"]), ("CAPEX", r["vp_capex_rateado"]),
             ("OPEX", r["vp_opex_rateado"])]
    fig, ax = plt.subplots(1, 2, figsize=(14, 4.6))
    fig.suptitle(f"Sub-bacia {sub_bacia} — {r['cidade']}   (das tabelas)",
                 fontsize=13, weight="bold")
    a = ax[0]; base = 0.0
    for i, (lab, v) in enumerate(itens):
        a.bar(i, v / 1e6, bottom=base / 1e6, color=(TEAL if v >= 0 else RED)); base += v
    a.bar(len(itens), base / 1e6, color=INK)
    a.set_xticks(range(len(itens) + 1))
    a.set_xticklabels([i[0] for i in itens] + ["VPL"], fontsize=8)
    a.axhline(0, color=GREY, lw=1); a.grid(alpha=.2, axis="y"); a.set_ylabel("R$ milhoes")
    a.set_title("Cascata do VPL  (run_subbacia)", weight="bold")
    a = ax[1]
    s = sa[sa.sub_bacia == sub_bacia].sort_values("ano") if sa is not None else None
    if s is not None and len(s) and s.receita_direta.sum() > 0:
        a.plot(s.ano, s.receita_direta / 1e6, color=TEAL, lw=2.4, marker="o", ms=3,
               label="receita direta")
        a.bar(s.ano, s.receita_indireta / 1e6, color=BLUE, alpha=.5, label="receita indireta")
        a.set_ylabel("R$ milhoes/ano"); a.legend(fontsize=8)
        a.set_title("Receita ao longo do tempo  (run_subbacia_ano)", weight="bold")
    else:
        a.text(.5, .5, "sub-bacia nao fatura neste plano", ha="center", va="center",
               fontsize=12, color=GREY, transform=a.transAxes)
        a.set_title("Receita ao longo do tempo", weight="bold")
    a.grid(alpha=.2)
    plt.tight_layout()
    if salvar:
        fig.savefig(salvar, dpi=120)
    return fig


def cobertura(T, cidades=None, salvar=None):
    cb = T["run_cobertura"]; mc = T["run_meta_cobertura"]
    sel = cidades or sorted(cb.cidade.unique())
    fig, ax = plt.subplots(1, 2, figsize=(15, 5.2))
    a = ax[0]
    cmap = plt.get_cmap("tab20")
    for i, c in enumerate(sel):
        d = cb[cb.cidade == c].sort_values("ano")
        a.plot(d.ano, d.cobertura_pct, lw=2.0, marker="o", ms=2.5, color=cmap(i % 20), label=c)
    for _, d in mc[mc.cidade.isin(sel)].iterrows():
        dentro = d.get("dentro_janela_capex", True)
        cor = (GREEN if d["atingida"] else RED) if dentro and pd.notna(d["atingida"]) else "none"
        a.scatter([d["ano"]], [d["pct_alvo"] * 100], color=cor, edgecolor=GREY if cor == "none" else "white",
                  s=52, marker="D", zorder=5, linewidths=.8)
    a.set_ylim(0, 105); a.grid(alpha=.2); a.set_ylabel("cobertura (%)")
    a.set_title("Cobertura por cidade  (losango vazado = meta fora da janela)",
                weight="bold", fontsize=11)
    a.legend(fontsize=7, ncol=2, loc="lower right")
    a = ax[1]
    agg = cb.groupby("ano").agg(c=("ligacoes_cobertas", "sum"), u=("universo", "sum")).reset_index()
    pct = agg.c / agg.u * 100
    a.fill_between(agg.ano, pct, color=TEAL, alpha=.20)
    a.plot(agg.ano, pct, color=TEAL, lw=2.8, marker="o", ms=3)
    a.annotate(f"{pct.iloc[-1]:.1f}%", (agg.ano.iloc[-1], pct.iloc[-1]),
               textcoords="offset points", xytext=(-30, 8), fontsize=11, weight="bold", color=INK)
    a.set_ylim(0, 105); a.grid(alpha=.2); a.set_ylabel("cobertura (%)")
    a.set_title("Cobertura agregada da regional", weight="bold", fontsize=11)
    plt.tight_layout()
    if salvar:
        fig.savefig(salvar, dpi=120)
    return fig


def cidade(T, nome):
    """Consolidado de uma cidade, so das tabelas."""
    cd = T["run_cidade"]; linha = cd[cd.cidade == nome]
    if linha.empty:
        print(f"cidade '{nome}' nao encontrada. Opcoes: {sorted(cd.cidade)}")
        return
    r = linha.iloc[0]
    mc = T["run_meta_cobertura"]; mc = mc[mc.cidade == nome].sort_values("ano")
    print("=" * 78)
    print(f"CIDADE {nome}")
    print("=" * 78)
    print(f"  sub-bacias {int(r['sub_bacias'])} | obras {int(r['obras_feitas'])} feitas, "
          f"{int(r['obras_fora'])} fora")
    print(f"  CAPEX {_brl(r['capex_total'])} | VPL {_brl(r['vpl'])}")
    print(f"  cobertura {r['cobertura_base_pct']:.1f}% -> {r['cobertura_final_pct']:.1f}%")
    if pd.notna(r.get("paridade_inicial")):
        print(f"  paridade {r['paridade_inicial']:.2f} -> {r['paridade_final']:.2f}")
    print("\n  METAS:")
    for _, d in mc.iterrows():
        dentro = d.get("dentro_janela_capex", True)
        if not dentro:
            print(f"    {int(d['ano'])}  alvo {d['pct_alvo']*100:5.1f}%   FORA DA JANELA DE CAPEX")
        else:
            mk = "OK  " if d["atingida"] else "FALHA"
            fal = (f"   faltam {d['deficit_ligacoes']:,.0f} lig"
                   if pd.notna(d["deficit_ligacoes"]) and d["deficit_ligacoes"] > 1 else "")
            print(f"    {int(d['ano'])}  alvo {d['pct_alvo']*100:5.1f}%   "
                  f"realizado {d['cobertura_ligacoes']:,.0f} lig   {mk}{fal}")
    print("=" * 78)
    return linha


# =============================================================================
#  TOPOLOGIA DO SISTEMA INTEIRO
#  Todas as sub-bacias convergindo para a ETE, com TODOS os componentes de cada
#  no — inclusive os que nao tem obra prevista, que aparecem com CAPEX 0.
# =============================================================================
COMPONENTES = [("lig", "Ligacao"), ("rede", "Rede"), ("tro", "Tronco"),
               ("eee", "EEE"), ("lr", "Linha de recalque")]
COMPONENTES_CTS = [("cts", "Coletor de tempo seco"), ("tro", "Tronco"),
                   ("eee", "EEE"), ("lr", "Linha de recalque")]


def listar_sistemas(T, cidade=None):
    """Sistemas disponiveis, com tamanho e situacao — para escolher qual desenhar."""
    si = T.get("run_sistema")
    if si is None or si.empty:
        sb = T["run_subbacia"]
        g = sb.groupby(["sistema", "cidade"]).agg(sub_bacias=("sub_bacia", "size"),
                                                  faturando=("faturando", "sum")).reset_index()
        return g[g.cidade == cidade] if cidade else g
    cols = [c for c in ["sistema", "cidade", "sub_bacias", "sub_bacias_faturando",
                        "modulos_construidos", "capacidade_instalada", "vazao_conectada",
                        "ocupacao_pct", "vazao_nao_atendida", "horizonte_anos"]
            if c in si.columns]
    d = si[cols]
    return d[d.cidade == cidade] if cidade else d


def _componente_de(obra_id, tipo):
    p = str(obra_id).split("_")[0].lower()
    if p in ("lig", "rede", "tro", "eee", "lr", "cts"):
        return p
    return "ete_mod" if tipo == "ete_mod" else ("ete" if tipo == "ete" else tipo)


def _depara_cts(T):
    """Pareamento bidirecional CTS<->sub-bacia a partir do snapshot da aba subbacia-cts."""
    par = {}
    snap = next((T[k] for k in T if str(k).endswith("subbacia_cts")), None)
    if snap is not None and {"sub_bacia", "cts"} <= set(snap.columns):
        for _, r in snap.iterrows():
            sbn, ctn = r.get("sub_bacia"), r.get("cts")
            if pd.notna(sbn) and pd.notna(ctn):
                par[sbn] = ctn; par[ctn] = sbn
    return par


def topologia_sistema(T, sistema, salvar=None, max_por_coluna=6):
    """Desenha o SISTEMA inteiro. Cada NO e um bloco ligado por jusante ate a ETE:
    sub-bacia tem 5 componentes (Ligacao + Rede + Tronco + EEE + Linha de recalque);
    no de CTS (cabecalho cyan, com '· CTS' e '↔ sub-bacia pareada') tem 4 (Coletor de
    tempo seco + Tronco + EEE + Linha de recalque). Verde = construido, laranja = nao
    construido, cinza = terceiro, vazado = sem obra prevista (CAPEX 0)."""
    sb = T["run_subbacia"]; ob = T["run_obra"]
    nos = sb[sb.sistema == sistema]
    if nos.empty:
        print(f"sistema '{sistema}' nao encontrado. Use L.listar_sistemas(T).")
        return
    ids = set(nos.sub_bacia)
    jus = dict(zip(nos.sub_bacia, nos.jusante))
    is_cts_no = dict(zip(nos.sub_bacia, nos.is_cts)) if "is_cts" in nos.columns else {}
    par_cts = _depara_cts(T)
    def _comps_do_no(nn):
        return COMPONENTES_CTS if is_cts_no.get(nn) else COMPONENTES

    # profundidade = saltos ate sair do sistema (chegar na ETE)
    def prof(n):
        d = 0; cur = n
        while cur in ids and d < 200:
            cur = jus.get(cur); d += 1
            if cur not in ids:
                break
        return d
    prof_no = {n: prof(n) for n in ids}
    dmax = max(prof_no.values())
    colunas = {}
    for n, d in prof_no.items():
        colunas.setdefault(dmax - d, []).append(n)      # 0 = mais distante da ETE
    for k in colunas:
        colunas[k] = sorted(colunas[k])
    ncol = dmax + 1
    nlin = max(len(v) for v in colunas.values())

    obs = ob[ob.obra_id.notna()].copy()
    if "componente" not in obs.columns:
        obs["componente"] = [_componente_de(i, t) for i, t in zip(obs.obra_id, obs.tipo)]
    por_no = {n: obs[obs.no == n] for n in ids}
    ete = obs[(obs.tipo.isin(["ete", "ete_mod"])) & (obs.sistema == sistema)]
    si = T.get("run_sistema")
    sirow = si[si.sistema == sistema].iloc[0] if (si is not None and not si.empty
                                                  and (si.sistema == sistema).any()) else None

    LW, HH = 4.35, 0.50
    TR, DR = 0.30, 0.235                       # altura da linha-titulo e das linhas de detalhe

    def _linhas_comp(o):
        """(marcador, cor, titulo_dir, [(rotulo, valor)]) de um componente."""
        if o is None:
            return "o", "#CBD5E1", "R$ 0", [("", "sem obra prevista")]
        terceiro = (o["responsavel"] == "Terceiro")
        constr = bool(o["construida"])
        cor = GREY if terceiro else (TEAL if constr else ORANGE)
        det = []
        pu, q = o.get("preco_unitario"), o.get("quantidade")
        un = o.get("unidade") or "un"
        if pu is not None and not pd.isna(pu):
            det.append(("preco unitario", f"{_brl(pu, 2)} / {un}"))
        if q is not None and not pd.isna(q):
            det.append(("quantidade", f"{q:,.1f} {un}"))
        if terceiro:
            det.append(("ano da obra", f"terceiro · prazo {int(o['prazo_meses'])}m"))
        elif constr:
            di = str(o.get("data_inicio") or "")
            ano = di[:4] if di else "-"
            mes = di[5:7] if len(di) >= 7 else ""
            det.append(("ano da obra", f"{ano}" + (f"   (inicio {mes}/{ano})" if mes else "")))
        else:
            det.append(("ano da obra", "NAO CONSTRUIDA"))
        return "s", cor, _brl(o["capex"]), det

    # altura do bloco de um no = cabecalho + 5 componentes
    def _alt_comp(o):
        return TR + len(_linhas_comp(o)[3]) * DR + 0.10
    alt_por_no = {}
    for n in ids:
        oo = None
        tot = 0.0
        for cod, _ in _comps_do_no(n):
            linha = por_no.get(n, obs.iloc[0:0])
            linha = linha[linha.componente == cod]
            tot += _alt_comp(linha.iloc[0] if len(linha) else None)
        alt_por_no[n] = HH + tot + 0.10
    BH = max(alt_por_no.values()) if alt_por_no else 2.0
    GX, GY = LW + 1.15, BH + 0.55
    fw = max(14.0, (ncol + 1) * GX + 0.8)
    fh = max(6.0, nlin * GY + 2.6)
    fig, a = plt.subplots(figsize=(fw, fh))
    a.set_xlim(0, (ncol + 1) * GX + 0.5); a.set_ylim(0, fh); a.axis("off")

    pos = {}
    ytopo = fh - 1.75
    for ci in range(ncol):
        nl = colunas.get(ci, [])
        y0 = ytopo - (nlin - len(nl)) * GY / 2.0
        for k, n in enumerate(nl):
            x = 0.40 + ci * GX; y = y0 - k * GY - BH
            pos[n] = (x, y)
            r = nos[nos.sub_bacia == n].iloc[0]
            fat = bool(r["faturando"])
            ehcts = bool(is_cts_no.get(n))
            hdr = CTS_COR if ehcts else (TEAL if fat else INK)
            a.add_patch(plt.Rectangle((x, y + BH - HH), LW, HH,
                                      facecolor=hdr, edgecolor="none", zorder=3))
            a.text(x + 0.12, y + BH - HH / 2, (f"{n}  ·  CTS" if ehcts else n),
                   fontsize=10, weight="bold", color="white", va="center", zorder=4)
            _hx = [f"vazao {r['vazao_marginal']:,.1f}"]
            if ehcts and par_cts.get(n):
                _hx.append(f"↔ {par_cts[n]}")
            if fat:
                _hx.append("FATURA")
            a.text(x + LW - 0.12, y + BH - HH / 2, "  ·  ".join(_hx),
                   fontsize=7.2, color="white", ha="right", va="center", zorder=4)
            a.add_patch(plt.Rectangle((x, y), LW, BH - HH, facecolor="white",
                                      edgecolor=(CTS_COR if ehcts else "#CBD5E1"),
                                      lw=(1.5 if ehcts else 1.1), zorder=2))
            yy = y + BH - HH - 0.06
            oo = por_no.get(n, obs.iloc[0:0])
            _comps = _comps_do_no(n)
            for ic, (cod, rot) in enumerate(_comps):
                linha = oo[oo.componente == cod]
                o = linha.iloc[0] if len(linha) else None
                marca, cor, cap, det = _linhas_comp(o)
                yy -= TR
                a.plot([x + 0.22], [yy + 0.09], marker=marca, ms=6.5, color=cor,
                       markerfacecolor=("white" if marca == "o" else cor), zorder=4)
                a.text(x + 0.40, yy + 0.09, rot, fontsize=8.4, weight="bold",
                       va="center", color=("#94A3B8" if marca == "o" else "#0F172A"), zorder=4)
                a.text(x + LW - 0.12, yy + 0.09, cap, fontsize=8.4, weight="bold", ha="right",
                       va="center", color=("#94A3B8" if marca == "o" else cor), zorder=4)
                for lab, val in det:
                    yy -= DR
                    if lab:
                        a.text(x + 0.52, yy + 0.08, lab, fontsize=7.0, va="center",
                               color="#94A3B8", zorder=4)
                        a.text(x + LW - 0.12, yy + 0.08, val, fontsize=7.3, va="center",
                               ha="right", color=("#7C2D12" if val == "NAO CONSTRUIDA" else "#334155"),
                               weight=("bold" if val == "NAO CONSTRUIDA" else "normal"), zorder=4)
                    else:
                        a.text(x + 0.52, yy + 0.08, val, fontsize=7.0, va="center",
                               style="italic", color="#94A3B8", zorder=4)
                yy -= 0.10
                if ic < len(_comps) - 1:
                    a.plot([x + 0.18, x + LW - 0.12], [yy + 0.04, yy + 0.04],
                           color="#F1F5F9", lw=0.9, zorder=3)

    # ---- ETE ----
    xe = 0.40 + ncol * GX
    ye = ytopo - BH
    hE = HH + len(ete) * (TR + 2 * DR + 0.10) + 0.95
    a.add_patch(plt.Rectangle((xe, ye + BH - hE), LW, hE, facecolor="#F5F3FF",
                              edgecolor=LILAC, lw=1.6, zorder=2))
    a.add_patch(plt.Rectangle((xe, ye + BH - HH), LW, HH, facecolor=LILAC,
                              edgecolor="none", zorder=3))
    a.text(xe + LW / 2, ye + BH - HH / 2, f"ETE · {sistema}", fontsize=10, weight="bold",
           color="white", ha="center", va="center", zorder=4)
    yy = ye + BH - HH - 0.06
    for _, o in ete.iterrows():
        # a ETE de referencia (tipo 'ete', CAPEX 0) e so o container do sistema, nao e obra
        ref = (o["tipo"] == "ete" and (o["capex"] or 0) <= 0)
        terceiro = (o["responsavel"] == "Terceiro")
        constr = bool(o["construida"])
        cor = GREY if (terceiro or ref) else (TEAL if constr else ORANGE)
        yy -= TR
        a.plot([xe + 0.22], [yy + 0.09], marker="s", ms=6.5, color=cor, zorder=4)
        a.text(xe + 0.40, yy + 0.09, str(o["obra_id"]), fontsize=8.2, weight="bold",
               va="center", color="#0F172A", zorder=4)
        a.text(xe + LW - 0.12, yy + 0.09, _brl(o["capex"]), fontsize=8.2, weight="bold",
               ha="right", va="center", color=cor, zorder=4)
        det = [("tipo", "ETE (modulo)" if o["tipo"] == "ete_mod" else "ETE (referencia)")]
        if ref:
            det.append(("ano da obra", "nao e obra — so referencia"))
        elif terceiro:
            det.append(("ano da obra", f"terceiro · prazo {int(o['prazo_meses'])}m"))
        elif constr:
            di = str(o.get("data_inicio") or ""); ano = di[:4]; mes = di[5:7] if len(di) >= 7 else ""
            det.append(("ano da obra", f"{ano}" + (f"   (inicio {mes}/{ano})" if mes else "")))
        else:
            det.append(("ano da obra", "NAO CONSTRUIDA"))
        for lab, val in det:
            yy -= DR
            a.text(xe + 0.52, yy + 0.08, lab, fontsize=7.0, va="center", color="#94A3B8", zorder=4)
            a.text(xe + LW - 0.12, yy + 0.08, val, fontsize=7.3, va="center", ha="right",
                   color=("#7C2D12" if val == "NAO CONSTRUIDA" else "#334155"),
                   weight=("bold" if val == "NAO CONSTRUIDA" else "normal"), zorder=4)
        yy -= 0.10
    if sirow is not None:
        cap = sirow.get("capacidade_instalada") or 0
        oc = sirow.get("ocupacao_pct")
        yy -= 0.16
        a.text(xe + 0.22, yy, f"capacidade instalada   {cap:,.0f}", fontsize=7.4, color="#4C1D95", zorder=4)
        yy -= 0.24
        a.text(xe + 0.22, yy, f"vazao conectada        {sirow.get('vazao_conectada', 0):,.1f}"
               + (f"  ({oc:.0f}%)" if pd.notna(oc) else ""), fontsize=7.4, color="#4C1D95", zorder=4)
        nao = sirow.get("vazao_nao_atendida") or 0
        if nao > 0:
            yy -= 0.24
            a.text(xe + 0.22, yy, f"vazao NAO atendida     {nao:,.1f}", fontsize=7.4,
                   color=RED, weight="bold", zorder=4)

    # ---- setas jusante ----
    for n, (x, y) in pos.items():
        alvo = jus.get(n)
        if alvo in pos:
            x2, y2 = pos[alvo]
        else:
            x2, y2 = xe, ye
        a.annotate("", xy=(x2 - 0.06, y2 + BH - HH / 2), xytext=(x + LW + 0.06, y + BH - HH / 2),
                   arrowprops=dict(arrowstyle="-|>", color="#94A3B8", lw=1.5,
                                   connectionstyle="arc3,rad=0.06"), zorder=1)

    nfat = int(nos.faturando.sum())
    _rot = str(sistema) if str(sistema).lower().startswith("sistema") else f"Sistema {sistema}"
    a.text(0.40, fh - 0.55, f"{_rot}  ·  {nos.iloc[0]['cidade']}", fontsize=14,
           weight="bold", color=INK)
    cx = sum(o["capex"] for _, o in obs[(obs.no.isin(ids)) | (obs.sistema == sistema)].iterrows()
             if bool(o["construida"]))
    a.text(0.40, fh - 0.95,
           f"{len(ids)} sub-bacias · {nfat} faturando · CAPEX construido {_brl(cx)}",
           fontsize=9.5, color=(GREEN if nfat else RED), weight="bold")
    leg = [("construida", TEAL, "s"), ("nao construida", ORANGE, "s"),
           ("terceiro", GREY, "s"), ("sem obra prevista (R$ 0)", "#CBD5E1", "o"),
           ("no CTS (↔ sub-bacia pareada)", CTS_COR, "s")]
    for k, (lab, cor, mk) in enumerate(leg):
        xx = 0.50 + k * min(3.4, (fw - 1.4) / len(leg))
        a.plot([xx], [0.35], marker=mk, ms=7, color=cor,
               markerfacecolor=("white" if mk == "o" else cor))
        a.text(xx + 0.18, 0.35, lab, fontsize=8, va="center", color="#334155")
    plt.tight_layout()
    if salvar:
        fig.savefig(salvar, dpi=125, bbox_inches="tight")
    return fig

def cobertura_cidade(T, cidade, salvar=None):
    """NIVEL 2 — a cidade em dois paineis:
       esquerda: cobertura ate o FIM DA CONCESSAO dela, com as metas DELA;
       direita : CAPEX, OPEX, receitas e VPL da cidade, em valor presente.
    As barras da direita somam exatamente o VPL — e a mesma decomposicao do rateio."""
    cb = T["run_cobertura"]; mc = T["run_meta_cobertura"]; m = T["run_meta"].iloc[0]
    sb = T["run_subbacia"]
    cidade = _valida(cidade, sorted(cb.cidade.unique()), "cidade")
    if cidade is None:
        return None
    d = cb[cb.cidade == cidade].sort_values("ano")
    if d.empty:
        print(f"sem serie de cobertura para {cidade}")
        return None

    # fim da concessao = maior horizonte entre os sistemas da cidade
    si = T.get("run_sistema")
    if si is not None and not si.empty and (si.cidade == cidade).any():
        hz = sorted(si[si.cidade == cidade].ano_fim_concessao.unique())
        fim = int(max(hz))
    else:
        fim = int(d.ano.max()); hz = [fim]
    d = d[d.ano <= fim]
    ab = int(m["ano_base"]); fim_capex = ab + int(m["anos_capex"]) - 1

    fig, ax = plt.subplots(1, 2, figsize=(16, 5.4),
                           gridspec_kw={"width_ratios": [1.35, 1]})

    # ---------------- esquerda: cobertura ate o fim da concessao ----------------
    a = ax[0]
    a.fill_between(d.ano, d.cobertura_pct, color=TEAL, alpha=.18)
    a.plot(d.ano, d.cobertura_pct, color=TEAL, lw=2.8, marker="o", ms=4)
    md = mc[mc.cidade == cidade]
    for _, r in md.iterrows():
        dentro = bool(r.get("dentro_janela_capex", True))
        y = r["pct_alvo"] * 100
        if dentro and pd.notna(r["atingida"]):
            cor = GREEN if r["atingida"] else RED
            a.scatter([r["ano"]], [y], color=cor, s=95, marker="D", zorder=5,
                      edgecolors="white", linewidths=1.1)
            txt = f"meta {int(r['ano'])}\n{y:.0f}%"
            if not r["atingida"] and pd.notna(r["deficit_ligacoes"]):
                txt += f"\nfaltam {r['deficit_ligacoes']:,.0f}"
            a.annotate(txt, (r["ano"], y), textcoords="offset points", xytext=(8, 6),
                       fontsize=8, color=cor, weight="bold")
        else:
            a.scatter([r["ano"]], [y], facecolor="none", edgecolor=GREY, s=95, marker="D",
                      zorder=5, linewidths=1.4)
            a.annotate(f"meta {int(r['ano'])}\n{y:.0f}% (fora da janela)", (r["ano"], y),
                       textcoords="offset points", xytext=(8, -20), fontsize=7.5, color=GREY)
    ini, ult = d.cobertura_pct.iloc[0], d.cobertura_pct.iloc[-1]
    a.axhline(ini, color=GREY, ls=":", lw=1.3)
    a.annotate(f"partida {ini:.1f}%", (d.ano.iloc[0], ini), textcoords="offset points",
               xytext=(4, -14), fontsize=8.5, color=GREY)
    if fim_capex < fim:
        a.axvline(fim_capex, color=ORANGE, ls="--", lw=1.5)
        a.text(fim_capex, 104.5, " fim do CAPEX", fontsize=8, color=ORANGE)
    a.axvline(fim, color=INK, ls="--", lw=1.8)
    a.text(fim, 101, "fim da concessao ", fontsize=8, color=INK, weight="bold", ha="right")
    a.annotate(f"{ult:.1f}%", (d.ano.iloc[-1], ult), textcoords="offset points",
               xytext=(-40, 10), fontsize=13, weight="bold", color=INK)
    a.set_xlim(d.ano.min() - 0.5, fim + 0.8); a.set_ylim(0, 108)
    from matplotlib.ticker import MaxNLocator
    a.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=9))
    a.grid(alpha=.25); a.set_ylabel("cobertura (%)")
    _dentro = md[md.get("dentro_janela_capex", True) == True] if len(md) else md
    ok = int(_dentro.atingida.fillna(False).sum()) if len(_dentro) else 0
    extra = "" if len(hz) < 2 else f"  ·  sistemas terminam entre {min(hz)} e {max(hz)}"
    a.set_title(f"Cobertura ate o fim da concessao ({fim})   ·   "
                f"{ok}/{len(_dentro)} metas na janela atingidas{extra}",
                fontsize=11.5, weight="bold", color=INK)

    # ---------------- direita: CAPEX, OPEX, receitas e VPL ----------------
    a = ax[1]
    c = sb[sb.cidade == cidade]
    itens = [("Receita\ndireta", c.vp_receita_direta.sum(), TEAL),
             ("Receita\nindireta", c.vp_receita_indireta.sum(), "#2DD4BF"),
             ("Efeito-base\nparidade", c.vp_efeito_base.sum(), "#5EEAD4"),
             ("CAPEX", c.vp_capex_rateado.sum(), RED),
             ("OPEX", c.vp_opex_rateado.sum(), ORANGE)]
    vpl = sum(v for _, v, _ in itens)
    itens.append(("VPL", vpl, INK))
    xs = range(len(itens))
    a.bar(xs, [v / 1e6 for _, v, _ in itens], color=[c3 for _, _, c3 in itens])
    for i, (lab, v, _) in enumerate(itens):
        dy = 6 if v >= 0 else -14
        a.annotate(f"{v/1e6:,.1f}", (i, v / 1e6), textcoords="offset points",
                   xytext=(0, dy), ha="center", fontsize=8.5,
                   weight=("bold" if lab == "VPL" else "normal"),
                   color=(INK if lab == "VPL" else "#334155"))
    a.set_xticks(list(xs)); a.set_xticklabels([i[0] for i in itens], fontsize=8)
    a.axhline(0, color=GREY, lw=1); a.grid(alpha=.25, axis="y")
    a.set_ylabel("R$ milhoes (valor presente)")
    cap_nom = None
    cd = T.get("run_cidade")
    if cd is not None and (cd.cidade == cidade).any():
        cap_nom = cd[cd.cidade == cidade].capex_total.iloc[0]
    sub = f"CAPEX nominal construido {_brl(cap_nom)}" if cap_nom is not None else ""
    a.set_title(f"Economia da cidade — as barras somam o VPL\n{sub}",
                fontsize=11.5, weight="bold", color=INK)

    fig.suptitle(f"{cidade}", fontsize=15, weight="bold", color=INK, y=1.0)
    plt.tight_layout()
    if salvar:
        fig.savefig(salvar, dpi=120, bbox_inches="tight")
    return fig
