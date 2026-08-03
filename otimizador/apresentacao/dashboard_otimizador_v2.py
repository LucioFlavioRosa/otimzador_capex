# =============================================================================
#  DASHBOARD DE RESULTADOS DO OTIMIZADOR DE CAPEX — ESGOTO
#  Camada de LEITURA dos resultados. Nao altera engine nem solver.
#
#  Niveis de leitura:
#    1) painel_geral(cen,res)          -> KPIs + 6 graficos do plano inteiro
#    2) tabela_obras(cen,res)          -> por que CADA obra entrou ou ficou de fora
#       explicar_obra(cen,res,oid)     -> o mesmo, em texto, para uma obra
#    3) deep_dive_subbacia(cen,res,sb) -> anatomia do VPL de uma sub-bacia
#    4) visao_cidade(cen,res,cid)      -> consolidado da cidade + metas + paridade
#    5) grafico_cobertura(cen,res)     -> cobertura ao longo do tempo vs metas
#    dashboard(cen,res)                -> tudo acima com menus (ipywidgets/Colab)
#
#  Requer: o engine (otimizador_capex_vNN) importado como M em quem chama, ou
#  passado em set_engine(M). Sem OR-Tools: so le o 'res' que ja veio do solver.
# =============================================================================
import math
from collections import defaultdict

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

# ---------------------------------------------------------------- paleta Peers
TEAL = "#0D9488"; INK = "#0F2E2B"; ORANGE = "#B45309"; RED = "#B91C1C"
BLUE = "#1C7293"; GREY = "#94A3B8"; GREEN = "#15803D"; LILAC = "#7C3AED"
COR_TIPO = {"coleta": TEAL, "rede": BLUE, "transporte": ORANGE,
            "ete": LILAC, "ete_mod": LILAC}
NOME_TIPO = {"coleta": "Ligacao", "rede": "Rede", "transporte": "Transporte",
             "ete": "ETE", "ete_mod": "ETE (modulo)"}
# Nome PRECISO do elemento: Transporte NUNCA e agrupado — vira Tronco / EEE / Linha de recalque
_ELEM_NOME = {"lig": "Ligacao", "rede": "Rede", "tro": "Tronco", "eee": "EEE", "lr": "Linha de recalque"}
_COR_ELEM  = {"Ligacao": TEAL, "Rede": BLUE, "Tronco": ORANGE, "EEE": "#0EA5E9",
              "Linha de recalque": "#A16207", "ETE (modulo)": LILAC, "ETE": LILAC}
def _elemento_nome(o):
    """Nome do elemento a partir do prefixo do id (lig/rede/tro/eee/lr) ou tipo (ETE)."""
    tp = getattr(o, "tipo", None)
    if tp in ("ete", "ete_mod"):
        return NOME_TIPO.get(tp, tp)
    return _ELEM_NOME.get(str(getattr(o, "id", "")).split("_")[0].lower(), NOME_TIPO.get(tp, str(tp)))

_M = None


def set_engine(mod):
    """Registra o modulo do engine (otimizador_capex_vNN) usado pelo dashboard."""
    global _M
    _M = mod
    return _M


def _eng():
    if _M is None:
        raise RuntimeError("engine nao registrado: chame dashboard_otimizador.set_engine(M)")
    return _M


# =============================================================================
#  HELPERS
# =============================================================================
def _ano_base(cen):
    ab = getattr(cen, "ano_base", None)
    return min(ab.values()) if ab else 2026


def _mes_para_data(cen, m):
    """Mes interno 0-based -> 'MM/AAAA'."""
    if m is None:
        return "-"
    ab = _ano_base(cen)
    return f"{(m % 12) + 1:02d}/{ab + m // 12}"


def _brl(v, casas=0):
    return f"R$ {v:,.{casas}f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _mm(v):
    """R$ em milhoes, compacto."""
    return f"{v/1e6:,.1f}".replace(",", "@").replace(".", ",").replace("@", ".")


def capex_unitario(o):
    """'2.472,6 m x R$ 449,99/m' — vazio se o banco nao trouxe os unitarios."""
    q = getattr(o, "quantidade", None); pu = getattr(o, "preco_unitario", None)
    if q is None or pu is None:
        return ""
    u = getattr(o, "unidade", None) or "un"
    return f"{q:,.1f} {u} x {_brl(pu, 2)}/{u}"


def _requisitos_de(cen, o):
    """Obras necessarias para a sub-bacia de uma coleta faturar."""
    return _eng().requisitos(cen, o)


def _mapa_exigencias(cen):
    """{obra_id: [sub-bacias que dependem dela]} — mesma topologia do rateio por vazao."""
    by_rede = defaultdict(list); by_transp = defaultdict(list)
    for q in cen.obras.values():
        if q.tipo == "rede":
            by_rede[q.no].append(q.id)
        elif q.tipo == "transporte":
            by_transp[q.no].append(q.id)
    req_sb = defaultdict(list)
    for c in cen.coletas:
        X = c.no
        ids = [c.id] + by_rede.get(X, [])
        for n in _eng().caminho(cen, X):
            ids += by_transp.get(n, [])
        sis = cen.nos[X].sistema
        if sis in cen.ete_do_sistema:
            ids.append(cen.ete_do_sistema[sis].id)
        for rid in ids:
            req_sb[rid].append(X)
    # modulos de ETE servem o sistema inteiro
    sys_sub = defaultdict(list)
    for sb, no in cen.nos.items():
        sys_sub[no.sistema].append(sb)
    for sis, mods in (getattr(cen, "modulos_sis", {}) or {}).items():
        for m in mods:
            req_sb[m.id] = list(sys_sub.get(sis, []))
    return dict(req_sb)


def _cobertura_pct(cen, res):
    """{cidade: [pct por ano]} a partir de cobertura_sistema (que ja vem por CIDADE)."""
    cs = res.get("cobertura_sistema", {}) or {}
    mx = getattr(cen, "max_lig", {}) or {}
    out = {}
    for cid, serie in cs.items():
        teto = mx.get(cid, 0.0)
        out[cid] = [(v / teto * 100.0 if teto > 0 else 0.0) for v in serie]
    return out


def _anos_calendario(cen):
    ab = _ano_base(cen)
    return [ab + y for y in range(cen.anos)]


def _fmt_seguro(spec):
    """Converte um spec de formato ('{:,.0f}' ou callable) num formatador que
    NAO quebra com None/NaN — o Styler renderiza de forma preguicosa no Colab,
    entao um erro aqui escapa do try/except e aparece como erro de formatter."""
    def f(x):
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return "—"
        try:
            return spec(x) if callable(spec) else spec.format(x)
        except (ValueError, TypeError):
            return str(x)
    return f


def mostrar(df, fmt=None, linhas=25):
    """Exibe um DataFrame formatado. Cai para o DataFrame cru se faltar jinja2
    (o .style do pandas depende dele) ou se nao houver IPython."""
    d = df.head(linhas) if linhas else df
    try:
        from IPython.display import display
    except Exception:
        print(d.to_string())
        return
    # formatadores a prova de None/NaN (so para colunas presentes)
    fmt_ok = ({c: _fmt_seguro(s) for c, s in fmt.items() if c in d.columns}
              if isinstance(fmt, dict) else fmt)
    try:
        display(d.style.format(fmt_ok, na_rep="—") if fmt_ok else d)
    except Exception:
        display(d)


# =============================================================================
#  1) PAINEL GERAL
# =============================================================================
def kpis(cen, res):
    """DataFrame de uma coluna com os numeros de cabeceira do plano."""
    reg = list(cen.regionais)[0]
    anos = cen.anos
    capex = sum(res["capex_ano"][reg])
    opex = sum(res["opex_ano"])
    receita = sum(res.get("receita_ano", [0.0] * anos))
    feitas = [oid for oid, y in res["plano"].items()
              if y is not None and cen.obras[oid].eh_aegea() and cen.obras[oid].necessaria]
    fora = [oid for oid, o in cen.obras.items()
            if o.necessaria and o.eh_aegea() and res["plano"].get(oid) is None]
    fat = [oid for oid, e in (res.get("elig") or {}).items() if e]
    lig = sum(cen.obras[oid].lig for oid in fat)
    mx = sum((getattr(cen, "max_lig", {}) or {}).values())
    cobfim = 0.0
    cs = res.get("cobertura_sistema") or {}
    if cs and mx > 0:
        cobfim = sum(s[-1] for s in cs.values()) / mx * 100.0
    det = res.get("metas_detalhe") or []
    ok = sum(1 for d in det if d["atingida"])
    linhas = [
        ("VPL do plano", _brl(res["vpl"])),
        ("CAPEX total (nominal)", _brl(capex)),
        ("OPEX total (nominal)", _brl(opex)),
        ("Receita total (nominal)", _brl(receita)),
        ("Obras construidas", f"{len(feitas)}"),
        ("Obras nao construidas", f"{len(fora)}"),
        ("Obrigatorias construidas", f"{res.get('obrig_construidas','-')}/{res.get('obrig_total','-')}"),
        ("Obrigatorias desconsideradas (fora da janela)",
         f"{len(res.get('obrig_desconsideradas_fora_janela') or [])}"),
        ("Sub-bacias faturando", f"{len(fat)}"),
        ("Ligacoes novas atendidas", f"{lig:,.0f}".replace(",", ".")),
        ("Cobertura no fim da concessao", f"{cobfim:.1f}%"),
        ("Metas de cobertura atingidas", f"{ok}/{len(det)}" if det else "sem metas"),
        ("VP do efeito-base da paridade", _brl(res.get("vp_efeito_base", 0.0) or 0.0)),
        ("Status do solver", str(res.get("milp_status", "-"))),
    ]
    return pd.DataFrame(linhas, columns=["Indicador", "Valor"]).set_index("Indicador")


def painel_geral(cen, res, salvar=None):
    """6 paineis: caixa por ano, acumulado, cascata do VPL, CAPEX por tipo,
    cobertura vs metas e distribuicao do VPL por sub-bacia."""
    reg = list(cen.regionais)[0]
    anos = cen.anos
    yrs = _anos_calendario(cen)
    capex = [res["capex_ano"][reg][y] / 1e6 for y in range(anos)]
    opex = [res["opex_ano"][y] / 1e6 for y in range(anos)]
    rec = [(res.get("receita_ano") or [0.0] * anos)[y] / 1e6 for y in range(anos)]

    fig, ax = plt.subplots(3, 2, figsize=(15, 13))
    fig.suptitle(f"Plano do otimizador — {reg}   |   VPL {_brl(res['vpl'])}   |   {res.get('milp_status','')}",
                 fontsize=15, weight="bold", y=0.997)

    # (1) CAPEX + OPEX + receita por ano
    a = ax[0, 0]
    a.bar(yrs, capex, color=TEAL, label="CAPEX")
    a.bar(yrs, opex, bottom=capex, color=ORANGE, label="OPEX")
    a.plot(yrs, rec, color=INK, lw=2.2, marker="o", ms=3, label="Receita")
    a.set_ylabel("R$ milhoes/ano"); a.grid(alpha=.2)
    a.set_title("Desembolso e receita por ano", weight="bold")
    a.legend(fontsize=9)

    # (2) fluxo de caixa e acumulado
    a = ax[0, 1]
    fc = [rec[y] - capex[y] - opex[y] for y in range(anos)]
    acc = []; s = 0.0
    for v in fc:
        s += v; acc.append(s)
    a.bar(yrs, fc, color=[TEAL if v >= 0 else RED for v in fc], label="do ano")
    a.plot(yrs, acc, color=INK, lw=2.2, marker="o", ms=3, label="acumulado")
    a.axhline(0, color=GREY, lw=1)
    vira = next((yrs[i] for i, v in enumerate(acc) if v >= 0), None)
    if vira:
        a.annotate(f"caixa acumulado\nvira positivo em {vira}", (vira, 0),
                   textcoords="offset points", xytext=(8, -34), fontsize=8.5,
                   color=GREEN, weight="bold")
    a.set_ylabel("R$ milhoes"); a.grid(alpha=.2)
    a.set_title("Fluxo de caixa (receita - CAPEX - OPEX)", weight="bold")
    a.legend(fontsize=9)

    # (3) cascata do VPL — em VALOR PRESENTE, via decomposicao por sub-bacia
    a = ax[1, 0]
    dec = _eng().vpl_por_subbacia(cen, res)
    T = {k: sum(d[k] for d in dec.values())
         for k in ("capex", "opex", "rec_dir", "rec_ind", "efeito_base")}
    passos = [("Receita\ndireta", T["rec_dir"]), ("Receita\nindireta", T["rec_ind"]),
              ("Efeito-base\nparidade", T["efeito_base"]), ("CAPEX", T["capex"]),
              ("OPEX", T["opex"])]
    base = 0.0
    for i, (lab, val) in enumerate(passos):
        col = TEAL if val >= 0 else RED
        a.bar(i, val / 1e6, bottom=base / 1e6, color=col)
        a.text(i, (base + val / 2) / 1e6, f"{val/1e6:+,.0f}", ha="center", va="center",
               fontsize=8, color="white", weight="bold")
        base += val
    a.bar(len(passos), base / 1e6, color=INK)
    a.text(len(passos), base / 2e6, f"{base/1e6:,.0f}", ha="center", va="center",
           fontsize=9, color="white", weight="bold")
    a.set_xticks(range(len(passos) + 1))
    a.set_xticklabels([p[0] for p in passos] + ["VPL"], fontsize=8)
    a.axhline(0, color=GREY, lw=1)
    a.set_ylabel("R$ milhoes (valor presente)"); a.grid(alpha=.2, axis="y")
    a.set_title("Cascata do VPL — de onde vem, para onde vai", weight="bold")

    # (4) CAPEX por tipo de obra
    a = ax[1, 1]
    ct = defaultdict(float)
    for oid, o in cen.obras.items():
        if o.eh_aegea() and res["plano"].get(oid) is not None:
            ct[_elemento_nome(o)] += o.capex
    it = sorted(ct.items(), key=lambda kv: kv[1])
    a.barh([k for k, _ in it], [v / 1e6 for _, v in it],
           color=[_COR_ELEM.get(k, BLUE) for k, _ in it])
    tot = sum(ct.values()) or 1.0
    for i, (k, v) in enumerate(it):
        a.text(v / 1e6, i, f"  {v/1e6:,.0f} ({v/tot*100:.0f}%)", va="center", fontsize=9)
    a.set_xlabel("R$ milhoes"); a.grid(alpha=.2, axis="x")
    a.set_title("CAPEX construido por elemento de obra", weight="bold")
    a.margins(x=.18)

    # (5) cobertura agregada vs metas
    a = ax[2, 0]
    cs = res.get("cobertura_sistema") or {}
    mx = sum((getattr(cen, "max_lig", {}) or {}).values()) or 1.0
    agg = [sum(cs.get(s, [0.0] * anos)[y] for s in cs) / mx * 100.0 for y in range(anos)]
    a.plot(yrs, agg, color=TEAL, lw=2.6, marker="o", ms=3, label="cobertura realizada")
    for d in (res.get("metas_detalhe") or []):
        ano = d["ano"]
        pct = d["pct"] * 100
        a.scatter([ano], [pct], color=(GREEN if d["atingida"] else RED),
                  zorder=5, s=45, marker="D")
    a.scatter([], [], color=GREEN, marker="D", s=45, label="meta atingida")
    a.scatter([], [], color=RED, marker="D", s=45, label="meta nao atingida")
    ac = getattr(cen, "anos_capex", anos)
    a.axvline(_ano_base(cen) + ac - 1, color=GREY, ls="--", lw=1.2)
    a.text(_ano_base(cen) + ac - 1, 100.5, " fim da janela de CAPEX", fontsize=8, color=GREY)
    a.set_ylim(0, 105); a.set_ylabel("cobertura (%)"); a.grid(alpha=.2)
    a.set_title("Cobertura ao longo do tempo vs metas", weight="bold")
    a.legend(fontsize=8, loc="lower right")

    # (6) VPL por sub-bacia
    a = ax[2, 1]
    vals = [d["vpl"] / 1e6 for d in dec.values()]
    a.hist(vals, bins=30, color=TEAL, edgecolor="white")
    a.axvline(0, color=RED, lw=1.4, ls="--")
    npos = sum(1 for v in vals if v > 0); nneg = len(vals) - npos
    a.set_xlabel("VPL da sub-bacia (R$ milhoes, com rateio por vazao)")
    a.set_ylabel("nº de sub-bacias"); a.grid(alpha=.2, axis="y")
    a.set_title(f"Distribuicao do VPL por sub-bacia ({npos} positivas, {nneg} negativas)",
                weight="bold")

    plt.tight_layout(rect=[0, 0, 1, 0.985])
    if salvar:
        fig.savefig(salvar, dpi=120)
    return fig


# =============================================================================
#  2) POR QUE CADA OBRA ENTROU OU SAIU
# =============================================================================
def _motivo_obra(cen, res, oid, req_sb, dec):
    """(status, categoria, motivo) de uma obra. Status: CONSTRUIDA | FORA | TERCEIRO | N/A.
    A categoria e o rotulo curto (agrupavel); o motivo e a frase completa."""
    o = cen.obras[oid]
    M = _eng()
    plano = res["plano"]; elig = res.get("elig") or {}
    y = plano.get(oid)
    fora_janela = set(res.get("obrig_desconsideradas_fora_janela") or [])

    if not o.necessaria:
        return ("N/A", "Nao e obra",
                "Sem CAPEX e sem prazo — nao e obra, so um no da topologia.")
    if not o.eh_aegea():
        return ("TERCEIRO", "Terceiro (pre-requisito)",
                f"Executada por terceiro (prazo {o.prazo}m, sem CAPEX Aegea). "
                f"Entra na cadeia como pre-requisito, nao consome orcamento.")

    # ---------- construida ----------
    if y is not None:
        deps = req_sb.get(oid, [])
        vaz = sum(cen.vazao.get(s, 0.0) for s in deps)
        pre = "OBRIGATORIA — travada na Fase 0. " if o.obrigatoria else ""
        if o.tipo == "coleta":
            if elig.get(oid):
                v = dec.get(o.no, {}).get("vpl", 0.0)
                ini = (res.get("inicio_fat") or {}).get(oid)
                return ("CONSTRUIDA",
                        "Obrigatoria (Fase 0)" if o.obrigatoria else "Habilita receita da sub-bacia",
                        f"{pre}Cadeia ate a ETE completa; fatura a partir de "
                        f"{_mes_para_data(cen, ini)}. VPL da sub-bacia (com rateio): {_brl(v)}.")
            return ("CONSTRUIDA", "Construida SEM receita (cadeia incompleta)",
                    "CONSTRUIDA MAS SEM RECEITA. "
                    + (res.get("motivo") or {}).get(oid, "cadeia incompleta."))
        nfat = sum(1 for s in deps if any(c.no == s and elig.get(c.id) for c in cen.coletas))
        return ("CONSTRUIDA",
                "Obrigatoria (Fase 0)" if o.obrigatoria else "Compartilhada — habilita sub-bacias",
                f"{pre}Obra de {NOME_TIPO.get(o.tipo, o.tipo).lower()} compartilhada: habilita "
                f"{nfat} de {len(deps)} sub-bacia(s) dependentes (vazao somada {vaz:,.1f}). "
                f"Custo rateado por vazao entre elas.")

    # ---------- nao construida: qual restricao mordeu? ----------
    if getattr(o, "proibida_nunca", False):
        return ("FORA", "Proibida no banco",
                "PROIBIDA no banco (proibida_ate = -1): o otimizador nunca pode escolher.")
    if oid in fora_janela:
        return ("FORA", "Obrigatoria fora da janela de CAPEX",
                "Era OBRIGATORIA, mas o ano exigido nao cabe na janela de CAPEX "
                "— desconsiderada (virou opcional) e nao foi escolhida.")
    starts = M.meses_permitidos(cen, o)
    Hm = int(getattr(cen, "anos_capex", cen.anos)) * 12
    if not starts:
        lim = getattr(o, "inicio_min", 0)
        return ("FORA", "Nao cabe na janela de CAPEX",
                f"Nao existe mes de inicio viavel: inicio minimo mes {lim} "
                f"({_mes_para_data(cen, lim)}) + prazo {o.prazo}m nao conclui dentro "
                f"da janela de CAPEX ({Hm}m).")
    if o.tipo == "coleta":
        ec = economia_potencial(cen, res, o.no)
        reqs = [r for r in _requisitos_de(cen, o) if r.eh_aegea() and r.id != oid]
        falt = [r for r in reqs if plano.get(r.id) is None]
        feitos = [r for r in reqs if plano.get(r.id) is not None]
        conta = ""
        if ec and ec.get("viavel"):
            extra = (f", ou {_brl(ec['saldo_rateado'])} se as vizinhas entrarem junto e ratearem as "
                     f"obras compartilhadas"
                     if abs(ec['vp_capex_rateado'] - ec['vp_capex_solo']) > 1 else "")
            conta = (f" A conta dela: {_brl(ec['vp_receita'])} de receita contra "
                     f"{_brl(ec['vp_capex_solo'])} de CAPEX a construir e {_brl(ec['vp_opex'])} de "
                     f"OPEX — saldo {_brl(ec['saldo_solo'])}{extra}.")
        # --- (a) parte da cadeia JA esta no plano e um elo especifico trava ---
        if falt and feitos:
            el = elo_critico(cen, res, o.no)
            oid_e, tp_e, cx_e, mot_e = el if el else (falt[0].id, NOME_TIPO.get(falt[0].tipo, "-"),
                                                      falt[0].capex, "nao foi selecionada")
            jaf = sum(r.capex for r in feitos)
            paga = bool(ec and ec.get("viavel") and ec["saldo_rateado"] >= 0)
            return ("FORA", "Travada por obra da cadeia",
                    f"O plano ja construiu {len(feitos)} obra(s) desta cadeia ({_brl(jaf)}), mas ela "
                    f"nao fatura porque ainda faltam {len(falt)}. O elo que trava e {oid_e} [{tp_e}], "
                    f"de {_brl(cx_e)} — que ficou de fora porque {mot_e}."
                    + (" Vale olhar: a sub-bacia se pagaria, entao o gargalo e a cadeia, nao a "
                       "economia dela." if paga else "") + conta)
        # --- (b) a cadeia inteira ficou de fora: e decisao economica, nao bloqueio ---
        if ec and ec.get("viavel") and ec["saldo_solo"] < 0 and ec["saldo_rateado"] < 0:
            return ("FORA", "Nao se paga",
                    f"Nao ha bloqueio: a sub-bacia inteira ficou de fora porque nao se paga. Ligar "
                    f"as {o.lig:,.0f} ligacoes exigiria construir {len(falt)+1} obra(s), e nem "
                    f"sozinha nem rateando com as vizinhas a receita cobre o investimento. So "
                    f"entraria por obrigacao contratual ou para cumprir meta de cobertura." + conta)
        if ec and ec.get("viavel") and ec["saldo_solo"] < 0 <= ec["saldo_rateado"]:
            return ("FORA", "So se paga em conjunto",
                    f"Sozinha ela nao cobre o custo da cadeia, mas com o rateio por vazao das obras "
                    f"compartilhadas o saldo vira positivo. Ou seja: so faz sentido entrar em bloco "
                    f"com as vizinhas que usam as mesmas obras." + conta)
        _sat = [str(a) for a, _, _, u in _uso_orcamento(cen, res) if u >= 95.0]
        return ("FORA", "Perdeu a disputa pelo orcamento",
                f"Ela se pagaria, mas perdeu a disputa por CAPEX: {_brl(o.capex)} para "
                f"{o.lig:,.0f} ligacoes ({_brl(o.capex / o.lig) if o.lig else '-'}/ligacao) rende "
                f"menos VPL por real do que as obras escolhidas."
                + (f" Anos com o teto saturado: {', '.join(_sat)}." if _sat else "") + conta)
    deps = req_sb.get(oid, [])
    return ("FORA", "Compartilhada nao acionada",
            f"Obra compartilhada nao acionada: nenhuma das {len(deps)} sub-bacia(s) "
            f"que dependem dela foi selecionada, entao seu CAPEX "
            f"({_brl(o.capex)}) nao teria receita para pagar.")


def tabela_obras(cen, res, status=None, cidade=None, tipo=None, ordenar="capex"):
    """DataFrame com uma linha por obra e o MOTIVO de ter entrado ou ficado de fora.
    status: 'CONSTRUIDA' | 'FORA' | 'TERCEIRO' | 'N/A' | None (todas)."""
    req_sb = _mapa_exigencias(cen)
    dec = _eng().vpl_por_subbacia(cen, res)
    linhas = []
    for oid, o in cen.obras.items():
        st, cat, mot = _motivo_obra(cen, res, oid, req_sb, dec)
        y = res["plano"].get(oid)
        rd = (res.get("ready") or {}).get(oid)
        linhas.append({
            "obra": oid,
            "tipo": NOME_TIPO.get(o.tipo, o.tipo),
            "cidade": cen.cidade_da(o),
            "sub_bacia": o.no if o.tipo not in ("ete", "ete_mod") else f"[sistema {o.sistema}]",
            "status": st,
            "categoria": cat,
            "obrigatoria": "SIM" if o.obrigatoria else "",
            "capex": o.capex,
            "quantidade": getattr(o, "quantidade", None),
            "unidade": getattr(o, "unidade", None),
            "preco_unitario": getattr(o, "preco_unitario", None),
            "opex_ano": o.opex_ano,
            "ligacoes": o.lig,
            "inicio": _mes_para_data(cen, y),
            "pronta": _mes_para_data(cen, rd),
            "fatura_em": _mes_para_data(cen, (res.get("inicio_fat") or {}).get(oid)),
            "elo_que_trava": "",
            "saldo_potencial": None,
            "motivo": mot,
        })
        if o.tipo == "coleta" and st == "FORA":
            ec = economia_potencial(cen, res, o.no)
            # so faz sentido nomear um "elo que trava" quando PARTE da cadeia ja foi construida
            el = elo_critico(cen, res, o.no) if cat == "Travada por obra da cadeia" else None
            linhas[-1]["elo_que_trava"] = (el[0] if el else "")
            if ec and ec.get("viavel"):
                linhas[-1]["saldo_potencial"] = ec["saldo_rateado"]
    df = pd.DataFrame(linhas)
    if status:
        df = df[df["status"] == str(status).upper()]
    if cidade:
        df = df[df["cidade"] == cidade]
    if tipo:
        df = df[df["tipo"].str.lower().str.startswith(str(tipo).lower())]
    if ordenar in df.columns:
        df = df.sort_values(ordenar, ascending=False)
    return df.reset_index(drop=True)


def explicar_obra(cen, res, oid):
    """Explicacao em texto de uma obra especifica."""
    if oid not in cen.obras:
        print(f"obra '{oid}' nao existe no cenario.")
        return
    o = cen.obras[oid]
    req_sb = _mapa_exigencias(cen)
    dec = _eng().vpl_por_subbacia(cen, res)
    st, cat, mot = _motivo_obra(cen, res, oid, req_sb, dec)
    y = res["plano"].get(oid); rd = (res.get("ready") or {}).get(oid)
    print("=" * 78)
    print(f"OBRA {oid}   [{_elemento_nome(o)}]   ->   {st}   ({cat})")
    print("=" * 78)
    print(f"  cidade / sub-bacia   : {cen.cidade_da(o)} / "
          f"{o.no if o.tipo not in ('ete','ete_mod') else '[sistema ' + str(o.sistema) + ']'}")
    print(f"  responsavel          : {o.responsavel}"
          f"{'   (OBRIGATORIA)' if o.obrigatoria else ''}")
    print(f"  CAPEX                : {_brl(o.capex)}"
          + (f"   ({', '.join(f'{k} {_brl(v)}' for k, v in o.capex_comp.items() if v > 0)})"
             if o.capex_comp else ""))
    _cu = capex_unitario(o)
    if _cu:
        print(f"     quantidade x preco : {_cu}")
    print(f"  OPEX/ano             : {_brl(o.opex_ano)}")
    print(f"  prazo de execucao    : {o.prazo} meses   |   inicio mais cedo permitido: "
          f"mes {o.inicio_min} ({_mes_para_data(cen, o.inicio_min)})")
    if o.tipo == "coleta":
        print(f"  ligacoes novas       : {o.lig:,.0f}   |   ticket {_brl(o.ticket_mes, 2)}/mes"
              f"   |   preco/ligacao {_brl(o.preco_ligacao, 2)}")
    _wo = getattr(o, "wacc_origem", "proprio")
    _wt = (" · médio da unidade" if _wo == "wacc_medio"
           else (" · próprio (financiamento contratado)" if _wo == "proprio" else ""))
    print(f"  WACC do elemento     : {(o.wacc or 0):.2%}{_wt}")
    print(f"  inicio no plano      : {_mes_para_data(cen, y)}   ->   pronta "
          f"{_mes_para_data(cen, rd)}")
    print()
    print("  POR QUE:")
    for ln in _quebra(mot, 72):
        print("    " + ln)
    deps = req_sb.get(oid, [])
    if deps and o.tipo != "coleta":
        elig = res.get("elig") or {}
        okn = [s for s in deps if any(c.no == s and elig.get(c.id) for c in cen.coletas)]
        print(f"\n  DEPENDEM DESTA OBRA: {len(deps)} sub-bacia(s); {len(okn)} faturando.")
        print(f"    {', '.join(deps[:12])}{' ...' if len(deps) > 12 else ''}")
    if o.tipo == "coleta":
        el = elo_critico(cen, res, o.no)
        trava = el[0] if el else None
        print("\n  CADEIA ATE A ETE (pre-requisitos):")
        for r in _requisitos_de(cen, o):
            yy = res["plano"].get(r.id)
            feito = (yy is not None or not r.eh_aegea())
            mk = "OK " if feito else ("XX " if r.id == trava else "-- ")
            tag = "   <== ELO QUE TRAVA" if r.id == trava else ""
            print(f"    {mk}{r.id:16} [{NOME_TIPO.get(r.tipo, r.tipo)}] "
                  f"{r.responsavel:9} {_brl(r.capex):>18}   inicio {_mes_para_data(cen, yy)}{tag}")
        ec = economia_potencial(cen, res, o.no)
        if ec and ec.get("viavel") and st == "FORA":
            print("\n  SE FOSSE LIGADA AGORA (valor presente):")
            print(f"    {'(+) Receita (direta + indireta)':<32}{_brl(ec['vp_receita']):>18}")
            print(f"    {'(-) CAPEX a construir, sozinha':<32}{_brl(ec['vp_capex_solo']):>18}")
            print(f"    {'(-) OPEX':<32}{_brl(ec['vp_opex']):>18}")
            print(f"    {'-'*50}")
            print(f"    {'SALDO sozinha':<32}{_brl(ec['saldo_solo']):>18}")
            if abs(ec['vp_capex_rateado'] - ec['vp_capex_solo']) > 1:
                print(f"    {'SALDO com rateio por vazao':<32}{_brl(ec['saldo_rateado']):>18}")
            print(f"    (comecaria a faturar em {_mes_para_data(cen, ec['inicio_faturamento'])}; "
                  f"{ec['obras_faltantes']} obra(s) a construir)")
    print("=" * 78)


def _quebra(txt, n):
    out = []; linha = ""
    for p in str(txt).split():
        if len(linha) + len(p) + 1 > n:
            out.append(linha); linha = p
        else:
            linha = (linha + " " + p).strip()
    if linha:
        out.append(linha)
    return out


# =============================================================================
#  DIAGNOSTICO: elo critico da cadeia e economia potencial da sub-bacia
# =============================================================================
def _uso_orcamento(cen, res):
    """[(ano_calendario, gasto, teto, uso%)] por ano da janela de CAPEX."""
    reg = list(cen.regionais)[0]
    ab = _ano_base(cen)
    g = res["capex_ano"][reg]; t = cen.orc[reg]
    n = int(getattr(cen, "anos_capex", cen.anos))
    return [(ab + y, g[y], t[y], (g[y] / t[y] * 100.0 if t[y] else 0.0)) for y in range(min(n, len(g), len(t)))]


def _por_que_elo_fora(cen, res, r, req_sb):
    """Motivo curto de um PRE-REQUISITO nao ter entrado (causa raiz de 1 nivel)."""
    M = _eng(); plano = res["plano"]
    if getattr(r, "proibida_nunca", False):
        return "proibida no banco"
    if r.id in set(res.get("obrig_desconsideradas_fora_janela") or []):
        return "era obrigatoria fora da janela de CAPEX e foi desconsiderada"
    if not M.meses_permitidos(cen, r):
        return f"nao cabe na janela de CAPEX (inicio minimo mes {getattr(r,'inicio_min',0)} + prazo {r.prazo}m)"
    deps = req_sb.get(r.id, [])
    if len(deps) > 1:
        elig = res.get("elig") or {}
        usando = sum(1 for sname in deps if any(c.no == sname and elig.get(c.id) for c in cen.coletas))
        if usando == 0:
            return (f"obra compartilhada por {len(deps)} sub-bacias e nenhuma delas entrou, "
                    f"entao ninguem paga o CAPEX de {_brl(r.capex)}")
    return f"nao foi selecionada: CAPEX de {_brl(r.capex)} sem receita que o justifique"


def elo_critico(cen, res, sb):
    """Qual pre-requisito trava a sub-bacia, e por que ELE ficou de fora.
    Retorna (obra_id, tipo, capex, motivo) ou None se a cadeia esta completa."""
    col = next((c for c in cen.coletas if c.no == sb), None)
    if col is None:
        return None
    plano = res["plano"]
    falt = [r for r in _requisitos_de(cen, col)
            if r.eh_aegea() and r.id != col.id and plano.get(r.id) is None]
    if not falt:
        return None
    req_sb = _mapa_exigencias(cen)
    r = max(falt, key=lambda q: q.capex)          # o elo mais caro e o que domina a decisao
    return (r.id, NOME_TIPO.get(r.tipo, r.tipo), r.capex, _por_que_elo_fora(cen, res, r, req_sb))


def economia_potencial(cen, res, sb):
    """Quanto a sub-bacia VALERIA se fosse ligada agora, em valor presente.
    Nao reotimiza: monta a cadeia faltante no mes mais cedo permitido e calcula
      VP(receita) - VP(CAPEX que falta) - VP(OPEX)
    'solo'    = esta sub-bacia paga sozinha todo o CAPEX que falta;
    'rateado' = paga so a fatia por vazao das obras compartilhadas (as demais entrariam juntas).
    Retorna dict ou None se a sub-bacia nao tem obra de coleta."""
    M = _eng()
    col = next((c for c in cen.coletas if c.no == sb), None)
    if col is None:
        return None
    plano = res["plano"]; req_sb = _mapa_exigencias(cen)
    reqs = _requisitos_de(cen, col)
    inicio_r = {}; pronto = 0
    for r in reqs:
        if not r.eh_aegea():
            pronto = max(pronto, r.prazo); continue
        y = plano.get(r.id)
        if y is None:
            st = M.meses_permitidos(cen, r)
            if not st:
                return {"viavel": False, "motivo": f"{r.id} nao cabe na janela de CAPEX"}
            y = min(st); inicio_r[r.id] = y
        pronto = max(pronto, y + r.prazo)
    ini = ((pronto // 12) + 1) * 12 + col.lag
    fat = (res.get("fator_esgoto_ano") or {}).get(cen.cidade_da(col))
    vp_rec = M._pv_receita(cen, col, ini, fat)
    cap_solo = cap_rat = 0.0
    for rid, y in inicio_r.items():
        r = cen.obras[rid]
        v = -M._pv_custo(cen, r, y)                       # _pv_custo devolve negativo
        deps = [d for d in req_sb.get(rid, []) if d in cen.nos]
        tot = sum(cen.vazao.get(d, 0.0) for d in deps)
        fr = (cen.vazao.get(sb, 0.0) / tot) if tot > 0 else 1.0
        cap_solo += v; cap_rat += v * fr
    vp_opex = 0.0
    for r in reqs:
        if r.opex_ano <= 0 or not r.eh_aegea():
            continue
        tx = cen.taxa_de(r); Hm = cen.horizonte(r) * 12
        vp_opex += sum((r.opex_ano / 12.0) / (1.0 + tx) ** (m // 12) for m in range(ini, Hm))
    return {"viavel": True, "sub_bacia": sb, "ligacoes": col.lig, "inicio_faturamento": ini,
            "vp_receita": vp_rec, "vp_capex_solo": cap_solo, "vp_capex_rateado": cap_rat,
            "vp_opex": vp_opex,
            "saldo_solo": vp_rec - cap_solo - vp_opex,
            "saldo_rateado": vp_rec - cap_rat - vp_opex,
            "obras_faltantes": len(inicio_r)}


def _obras_do_caminho(cen, sb):
    """[(no, [obras])] do caminho da sub-bacia ate a ETE, mais a coluna da ETE."""
    M = _eng()
    cam = M.caminho(cen, sb)
    cols = []
    for i, n in enumerate(cam):
        ob = []
        if i == 0:
            ob += [o for o in cen.obras.values() if o.tipo in ("coleta", "rede") and o.no == n]
        ob += [o for o in cen.obras.values() if o.tipo == "transporte" and o.no == n]
        cols.append((n, ob))
    sis = cen.nos[sb].sistema
    ete = []
    if sis in cen.ete_do_sistema:
        e = cen.ete_do_sistema[sis]
        if e.necessaria or e.capex > 0:
            ete.append(e)
    ete += list((getattr(cen, "modulos_sis", {}) or {}).get(sis, []))
    cols.append((f"ETE · {sis}", ete))
    return cols


def topologia_subbacia(cen, res, sb, salvar=None):
    """Desenha o caminho COMPLETO da sub-bacia ate a ETE, com todas as obras de cada no,
    coloridas pelo status no plano. Mostra onde a cadeia quebra e quem mais depende de
    cada obra compartilhada."""
    if sb not in cen.nos:
        print(f"sub-bacia '{sb}' nao existe.")
        return
    plano = res["plano"]; elig = res.get("elig") or {}
    req_sb = _mapa_exigencias(cen)
    colunas = _obras_do_caminho(cen, sb)
    el = elo_critico(cen, res, sb)
    trava = el[0] if el else None
    col = next((c for c in cen.coletas if c.no == sb), None)
    fatura = bool(col is not None and elig.get(col.id))

    ncol = len(colunas)
    nlin = max(len(o) for _, o in colunas) or 1
    LW, LH = 3.05, 1.16                                    # largura/altura de cada caixa
    fig_w = max(13.0, ncol * (LW + 0.62))
    fig_h = max(5.2, 3.1 + nlin * (LH + 0.30))
    fig, a = plt.subplots(figsize=(fig_w, fig_h))
    a.set_xlim(0, ncol * (LW + 0.62)); a.set_ylim(0, fig_h); a.axis("off")

    ytopo = fig_h - 1.75
    for i, (no, obras) in enumerate(colunas):
        x = 0.34 + i * (LW + 0.62)
        # ---- cabecalho do no ----
        eh_ete = str(no).startswith("ETE")
        eh_org = (i == 0)
        cor = LILAC if eh_ete else (INK if eh_org else BLUE)
        a.add_patch(plt.Rectangle((x, ytopo), LW, 0.52, facecolor=cor, edgecolor="none",
                                  zorder=3))
        rot = ("ORIGEM · " + str(no)) if eh_org else str(no)
        a.text(x + LW / 2, ytopo + 0.26, rot, ha="center", va="center", color="white",
               fontsize=9.5, weight="bold", zorder=4)
        if not eh_ete:
            vz = cen.vazao.get(no, 0.0)
            a.text(x + LW / 2, ytopo - 0.16, f"vazao {vz:,.1f}", ha="center", va="center",
                   fontsize=7.5, color=GREY, zorder=4)
        # ---- seta para o proximo no ----
        if i < ncol - 1:
            a.annotate("", xy=(x + LW + 0.56, ytopo + 0.26), xytext=(x + LW + 0.05, ytopo + 0.26),
                       arrowprops=dict(arrowstyle="-|>", color=GREY, lw=2.0))
        # ---- caixas das obras ----
        for j, o in enumerate(obras):
            y = ytopo - 0.62 - (j + 1) * (LH + 0.16)
            y0 = plano.get(o.id)
            terceiro = not o.eh_aegea()
            feito = (y0 is not None) or terceiro
            if o.id == trava:
                fc, ec, lw, tc = "#FEE2E2", RED, 2.6, RED
            elif terceiro:
                fc, ec, lw, tc = "#F1F5F9", GREY, 1.0, "#475569"
            elif feito:
                fc, ec, lw, tc = "#CCFBF1", TEAL, 1.4, INK
            else:
                fc, ec, lw, tc = "#FFF7ED", ORANGE, 1.4, "#7C2D12"
            a.add_patch(plt.Rectangle((x, y), LW, LH, facecolor=fc, edgecolor=ec,
                                      linewidth=lw, zorder=3))
            a.text(x + 0.10, y + LH - 0.22, o.id, fontsize=8.6, weight="bold", color=tc, zorder=4)
            a.text(x + 0.10, y + LH - 0.50,
                   f"{NOME_TIPO.get(o.tipo, o.tipo)} · {_brl(o.capex)}", fontsize=7.6,
                   color="#334155", zorder=4)
            if terceiro:
                sit = f"TERCEIRO · prazo {o.prazo}m"
            elif y0 is not None:
                sit = f"construida · inicio {_mes_para_data(cen, y0)}"
            else:
                sit = "NAO CONSTRUIDA"
            a.text(x + 0.10, y + LH - 0.76, sit, fontsize=7.6, weight="bold", color=tc, zorder=4)
            deps = [d for d in req_sb.get(o.id, []) if d in cen.nos]
            if len(deps) > 1:
                nf = sum(1 for d in deps
                         if any(c.no == d and elig.get(c.id) for c in cen.coletas))
                a.text(x + 0.10, y + LH - 1.01,
                       f"compartilhada: {len(deps)} sub-bacias, {nf} faturando",
                       fontsize=7.0, style="italic", color="#64748B", zorder=4)
            if o.id == trava:
                a.text(x + LW - 0.10, y + LH - 0.22, "ELO QUE TRAVA", ha="right", fontsize=7.4,
                       weight="bold", color=RED, zorder=4)

    # ---- titulo e veredito ----
    cid = cen.nos[sb].cidade
    cab = f"Topologia de {sb}  ·  {cid}  ·  sistema {cen.nos[sb].sistema}"
    a.text(0.34, fig_h - 0.44, cab, fontsize=13.5, weight="bold", color=INK)
    if fatura:
        sub = (f"FATURA a partir de "
               f"{_mes_para_data(cen, (res.get('inicio_fat') or {}).get(col.id))} — cadeia completa.")
        scor = GREEN
    else:
        ec_ = economia_potencial(cen, res, sb)
        falta = sum(1 for _, obs in colunas for o in obs
                    if o.eh_aegea() and plano.get(o.id) is None and o.capex > 0)
        sub = f"NAO FATURA — {falta} obra(s) da cadeia fora do plano"
        if el:
            sub += f"; o elo que trava e {el[0]}"
        if ec_ and ec_.get("viavel"):
            sub += f". Saldo se fosse ligada: {_brl(ec_['saldo_rateado'])} (com rateio)"
        scor = RED
    a.text(0.34, fig_h - 0.80, sub, fontsize=9.6, color=scor, weight="bold")

    leg = [("construida", "#CCFBF1", TEAL), ("nao construida", "#FFF7ED", ORANGE),
           ("elo que trava", "#FEE2E2", RED), ("terceiro", "#F1F5F9", GREY)]
    _passo = min(2.75, max(1.55, (ncol * (LW + 0.62) - 0.9) / 4.0))
    for k, (lab, fc, ec2) in enumerate(leg):
        xx = 0.34 + k * _passo
        a.add_patch(plt.Rectangle((xx, 0.22), 0.34, 0.26, facecolor=fc, edgecolor=ec2, lw=1.4))
        a.text(xx + 0.44, 0.35, lab, fontsize=8, va="center", color="#334155")
    plt.tight_layout()
    if salvar:
        fig.savefig(salvar, dpi=125, bbox_inches="tight")
    return fig


def topologia_texto(cen, res, sb):
    """Mesma topologia em texto (util para log e para colar em e-mail)."""
    if sb not in cen.nos:
        print(f"sub-bacia '{sb}' nao existe.")
        return
    plano = res["plano"]; elig = res.get("elig") or {}
    req_sb = _mapa_exigencias(cen)
    el = elo_critico(cen, res, sb); trava = el[0] if el else None
    print("=" * 78)
    print(f"CAMINHO DE {sb} ATE A ETE   ({cen.nos[sb].cidade} / {cen.nos[sb].sistema})")
    print("=" * 78)
    for i, (no, obras) in enumerate(_obras_do_caminho(cen, sb)):
        seta = "" if i == 0 else "   |\n   v\n"
        print(seta + f"[{no}]" + ("   <- ORIGEM" if i == 0 else ""))
        for o in obras:
            y = plano.get(o.id)
            if not o.eh_aegea():
                mk, sit = "  ~~", f"terceiro, prazo {o.prazo}m"
            elif y is not None:
                mk, sit = "  OK", f"inicio {_mes_para_data(cen, y)}"
            else:
                mk, sit = ("  XX" if o.id == trava else "  --"), "NAO CONSTRUIDA"
            deps = [d for d in req_sb.get(o.id, []) if d in cen.nos]
            comp = ""
            if len(deps) > 1:
                nf = sum(1 for d in deps if any(c.no == d and elig.get(c.id) for c in cen.coletas))
                comp = f"  [compartilhada: {len(deps)} sub-bacias, {nf} faturando]"
            tag = "   <== ELO QUE TRAVA" if o.id == trava else ""
            _q = getattr(o, "quantidade", None)
            _qt = f"  ({_q:,.1f} {getattr(o,'unidade',None) or 'un'})" if _q is not None else ""
            print(f"{mk} {o.id:<18}{NOME_TIPO.get(o.tipo,o.tipo):<14}{_brl(o.capex):>16}{_qt}   {sit}{comp}{tag}")
    print("=" * 78)


def por_que_nao(cen, res, sb, topologia=True):
    """Narrativa completa de por que uma sub-bacia nao entrou no plano.
    Com topologia=True, desenha tambem o caminho ate a ETE."""
    if sb not in cen.nos:
        print(f"sub-bacia '{sb}' nao existe.")
        return
    col = next((c for c in cen.coletas if c.no == sb), None)
    elig = res.get("elig") or {}
    print("=" * 78)
    print(f"POR QUE {sb} NAO ENTROU   ({cen.nos[sb].cidade} / sistema {cen.nos[sb].sistema})")
    print("=" * 78)
    if col is not None and elig.get(col.id):
        print("  Ela ENTROU: cadeia completa e faturando a partir de "
              f"{_mes_para_data(cen, (res.get('inicio_fat') or {}).get(col.id))}.")
        print("=" * 78)
        return
    ec = economia_potencial(cen, res, sb)
    el = elo_critico(cen, res, sb)
    reqs = [r for r in _requisitos_de(cen, col) if r.eh_aegea() and r.id != col.id] if col else []
    falt = [r for r in reqs if res["plano"].get(r.id) is None]
    feitos = [r for r in reqs if res["plano"].get(r.id) is not None]
    print("  1) A CADEIA ATE A ETE")
    if not falt:
        print("     Completa — o que faltou foi a propria obra de coleta.\n")
    elif feitos and el:
        oid, tp, cx, mot = el
        print(f"     Parcial: {len(feitos)} obra(s) ja no plano ({_brl(sum(r.capex for r in feitos))}), "
              f"{len(falt)} faltando.")
        print(f"     O elo que trava e {oid} [{tp}], de {_brl(cx)}.")
        for ln in _quebra("Ele ficou de fora porque " + mot + ".", 68):
            print("     " + ln)
        print()
    else:
        print(f"     Ausente: nenhuma das {len(reqs)} obras da cadeia entrou no plano.")
        print("     Nao ha um elo isolado travando — a sub-bacia inteira foi preterida.\n")
    print("  2) A CONTA DA SUB-BACIA (valor presente, se fosse ligada agora)")
    if not ec or not ec.get("viavel"):
        print(f"     Nao da para ligar: {(ec or {}).get('motivo','sem obra de coleta')}.")
    else:
        print(f"     {'(+) Receita (direta + indireta)':<34}{_brl(ec['vp_receita']):>18}")
        print(f"     {'(-) CAPEX a construir, sozinha':<34}{_brl(ec['vp_capex_solo']):>18}")
        print(f"     {'(-) OPEX':<34}{_brl(ec['vp_opex']):>18}")
        print(f"     {'-'*52}")
        print(f"     {'SALDO se pagar tudo sozinha':<34}{_brl(ec['saldo_solo']):>18}")
        if abs(ec['vp_capex_rateado'] - ec['vp_capex_solo']) > 1:
            print(f"     {'SALDO com rateio por vazao':<34}{_brl(ec['saldo_rateado']):>18}"
                  "   (se as vizinhas entrarem junto)")
        print()
        print("  3) VEREDITO")
        if ec['saldo_solo'] < 0 and ec['saldo_rateado'] < 0:
            print("     NAO SE PAGA. Mesmo dividindo as obras compartilhadas com as vizinhas, a")
            print("     receita nao cobre o investimento. So entraria por obrigacao contratual ou")
            print("     para cumprir meta de cobertura.")
            if falt and feitos:
                print("     Ou seja: o elo faltante acima e o sintoma, nao a causa. Mesmo que ele")
                print("     fosse construido, esta sub-bacia continuaria destruindo valor.")
        elif ec['saldo_solo'] < 0 <= ec['saldo_rateado']:
            print("     SO SE PAGA EM CONJUNTO. Sozinha ela nao cobre o custo da cadeia, mas se")
            print("     entrar junto com as vizinhas que usam as mesmas obras, o rateio a viabiliza.")
        else:
            print(f"     SE PAGARIA ({_brl(ec['saldo_rateado'])}).")
            if falt and feitos:
                print("     O problema e a CADEIA: destravar o elo acima liberaria uma sub-bacia")
                print("     que gera valor. Vale avaliar como candidata prioritaria.")
            else:
                print("     Perdeu a disputa pelo orcamento: o dinheiro foi para obras com mais")
                print("     VPL por real investido.")
            ap = [(a, u) for a, _, _, u in _uso_orcamento(cen, res) if u >= 95.0]
            if ap:
                print(f"     Anos com o teto saturado: {', '.join(str(a) for a, _ in ap)}.")
    print("=" * 78)
    if topologia:
        return topologia_subbacia(cen, res, sb)


# =============================================================================
#  3) DEEP DIVE — SUB-BACIA
# =============================================================================
def ranking_subbacias(cen, res, n=20, crescente=False):
    """Sub-bacias ordenadas por VPL (ja com rateio por vazao das obras compartilhadas)."""
    dec = _eng().vpl_por_subbacia(cen, res)
    elig = res.get("elig") or {}
    lin = []
    for sb, d in dec.items():
        c = next((c for c in cen.coletas if c.no == sb), None)
        lin.append({
            "sub_bacia": sb,
            "cidade": cen.nos[sb].cidade,
            "sistema": cen.nos[sb].sistema,
            "fatura": "SIM" if (c is not None and elig.get(c.id)) else "nao",
            "ligacoes": (c.lig if c else 0.0),
            "vazao": cen.vazao.get(sb, 0.0),
            "capex_rateado": d["capex"],
            "opex_rateado": d["opex"],
            "receita_dir": d["rec_dir"],
            "receita_ind": d["rec_ind"],
            "efeito_base": d["efeito_base"],
            "vpl": d["vpl"],
        })
    df = pd.DataFrame(lin).sort_values("vpl", ascending=crescente)
    return df.head(n).reset_index(drop=True) if n else df.reset_index(drop=True)


def deep_dive_subbacia(cen, res, sb, grafico=True):
    """Anatomia completa do VPL de uma sub-bacia."""
    if sb not in cen.nos:
        print(f"sub-bacia '{sb}' nao existe. Ex.: {list(cen.nos)[:5]}")
        return
    M = _eng()
    dec = M.vpl_por_subbacia(cen, res)
    d = dec.get(sb, {})
    no = cen.nos[sb]
    col = next((c for c in cen.coletas if c.no == sb), None)
    elig = res.get("elig") or {}
    fatura = bool(col is not None and elig.get(col.id))

    print("=" * 78)
    print(f"SUB-BACIA {sb}   |   cidade {no.cidade}   |   sistema {no.sistema}")
    print("=" * 78)
    print(f"  jusante              : {no.jusante}")
    print(f"  caminho ate a ETE    : {' -> '.join(M.caminho(cen, sb))} -> ETE")
    print(f"  vazao marginal       : {cen.vazao.get(sb, 0.0):,.2f}")
    sr = (getattr(cen, "sub_receita", {}) or {}).get(sb, {})
    if sr:
        print(f"  ligacoes existentes  : {sr.get('atuais', 0):,.0f}   |   ticket "
              f"{_brl(sr.get('ticket', 0), 2)}/mes   |   arrecadacao "
              f"{sr.get('arrec', 1):.1%}")
    if col:
        print(f"  ligacoes novas       : {col.lig:,.0f}   |   maturacao {col.mat}m   |   lag {col.lag}m")
    print()
    print(f"  SITUACAO: {'FATURA' if fatura else 'NAO FATURA'}", end="")
    if fatura:
        print(f" a partir de {_mes_para_data(cen, (res.get('inicio_fat') or {}).get(col.id))}")
    else:
        print("  ->  " + ((res.get("motivo") or {}).get(col.id, "obra de coleta nao construida.")
                          if col else "sem obra de coleta."))
    print()
    print("  DECOMPOSICAO DO VPL (valor presente, com rateio por vazao):")
    itens = [("Receita direta", d.get("rec_dir", 0.0)),
             ("Receita indireta (ligacao)", d.get("rec_ind", 0.0)),
             ("Efeito-base da paridade", d.get("efeito_base", 0.0)),
             ("CAPEX rateado", d.get("capex", 0.0)),
             ("OPEX rateado", d.get("opex", 0.0))]
    for lab, v in itens:
        print(f"    {lab:<28} {_brl(v):>20}")
    print(f"    {'-' * 48}")
    print(f"    {'VPL da sub-bacia':<28} {_brl(d.get('vpl', 0.0)):>20}")
    print()
    print("  OBRAS QUE ESTA SUB-BACIA EXIGE (e a fatia que ela paga):")
    if col:
        reqs = _requisitos_de(cen, col)
        req_sb = _mapa_exigencias(cen)
        for r in reqs:
            deps = req_sb.get(r.id, [sb])
            tot = sum(cen.vazao.get(s, 0.0) for s in deps) or 1.0
            fr = cen.vazao.get(sb, 0.0) / tot
            y = res["plano"].get(r.id)
            mk = "OK" if (y is not None or not r.eh_aegea()) else "--"
            print(f"    {mk} {r.id:16} [{NOME_TIPO.get(r.tipo, r.tipo):<13}] "
                  f"CAPEX {_brl(r.capex):>16}  x {fr:6.1%} = {_brl(r.capex * fr):>16}  "
                  f"({len(deps)} sub-bacia(s))")
    print("=" * 78)

    if not grafico:
        return
    fig, ax = plt.subplots(1, 2, figsize=(14, 4.6))
    fig.suptitle(f"Sub-bacia {sb} — {no.cidade}", fontsize=13, weight="bold")
    a = ax[0]
    base = 0.0
    labs = ["Rec.\ndireta", "Rec.\nindireta", "Efeito\nbase", "CAPEX", "OPEX"]
    for i, (lab, v) in enumerate(itens):
        a.bar(i, v / 1e6, bottom=base / 1e6, color=(TEAL if v >= 0 else RED))
        base += v
    a.bar(len(itens), base / 1e6, color=INK)
    a.set_xticks(range(len(itens) + 1)); a.set_xticklabels(labs + ["VPL"], fontsize=8)
    a.axhline(0, color=GREY, lw=1); a.grid(alpha=.2, axis="y")
    a.set_ylabel("R$ milhoes"); a.set_title("Cascata do VPL da sub-bacia", weight="bold")

    a = ax[1]
    if col and fatura:
        ini = (res.get("inicio_fat") or {})[col.id]
        Hm = cen.horizonte(col) * 12
        fe = (res.get("fator_esgoto_ano") or {}).get(no.cidade)
        rdm = col.receita_dir_regime() / 12.0
        serie = [0.0] * cen.anos
        for m in range(ini, Hm):
            k = m - ini; Y = m // 12
            f = (fe[Y] if (fe and Y < len(fe)) else (fe[-1] if fe else 1.0))
            if Y < cen.anos:
                serie[Y] += rdm * M._rampa(k, col.mat) * f
        a.plot(_anos_calendario(cen), [v / 1e6 for v in serie], color=TEAL, lw=2.4, marker="o", ms=3)
        a.axvline(_ano_base(cen) + ini // 12, color=ORANGE, ls="--", lw=1.4)
        a.text(_ano_base(cen) + ini // 12, max(serie) / 1e6 * .1 if max(serie) else 0,
               " inicio do faturamento", fontsize=8, color=ORANGE)
        a.set_ylabel("R$ milhoes/ano"); a.set_title("Receita direta ao longo do tempo (com rampa e paridade)",
                                                    weight="bold")
    else:
        a.text(.5, .5, "sub-bacia nao fatura neste plano", ha="center", va="center",
               fontsize=12, color=GREY, transform=a.transAxes)
        a.set_title("Receita ao longo do tempo", weight="bold")
    a.grid(alpha=.2)
    plt.tight_layout()
    return fig


# =============================================================================
#  4) VISAO DA CIDADE
# =============================================================================
def tabela_cidades(cen, res):
    """Consolidado por cidade: CAPEX, VPL, cobertura, metas, paridade."""
    dec = _eng().vpl_por_subbacia(cen, res)
    elig = res.get("elig") or {}
    plano = res["plano"]
    pct = _cobertura_pct(cen, res)
    det = res.get("metas_detalhe") or []
    fa = res.get("fator_esgoto_ano") or {}
    lin = []
    for cid in sorted({n.cidade for n in cen.nos.values()}):
        subs = [sb for sb, n in cen.nos.items() if n.cidade == cid]
        vpl = sum(dec.get(sb, {}).get("vpl", 0.0) for sb in subs)
        capex = sum(o.capex for oid, o in cen.obras.items()
                    if o.eh_aegea() and plano.get(oid) is not None and cen.cidade_da(o) == cid)
        feitas = sum(1 for oid, o in cen.obras.items()
                     if o.necessaria and o.eh_aegea() and cen.cidade_da(o) == cid
                     and plano.get(oid) is not None)
        fora = sum(1 for oid, o in cen.obras.items()
                   if o.necessaria and o.eh_aegea() and cen.cidade_da(o) == cid
                   and plano.get(oid) is None)
        lig = sum(c.lig for c in cen.coletas if cen.cidade_da(c) == cid and elig.get(c.id))
        md = [d for d in det if d["sistema"] == cid]
        fe = fa.get(cid) or []
        lin.append({
            "cidade": cid,
            "sub_bacias": len(subs),
            "obras_feitas": feitas,
            "obras_fora": fora,
            "capex": capex,
            "vpl": vpl,
            "lig_novas": lig,
            "cob_base_%": (cen.base_lig.get(cid, 0.0) / cen.max_lig.get(cid, 1.0) * 100.0),
            "cob_final_%": (pct.get(cid, [0.0])[-1]),
            "metas": f"{sum(1 for d in md if d['atingida'])}/{len(md)}" if md else "-",
            "paridade_ini": (fe[0] if fe else None),
            "paridade_fim": (fe[-1] if fe else None),
            "peso": (getattr(cen, "peso_cidade", {}) or {}).get(cid, 1.0),
        })
    return pd.DataFrame(lin).sort_values("vpl", ascending=False).reset_index(drop=True)


def visao_cidade(cen, res, cid, grafico=True):
    """Detalhe de uma cidade: metas, cobertura, obras e ranking de sub-bacias."""
    cidades = sorted({n.cidade for n in cen.nos.values()})
    if cid not in cidades:
        print(f"cidade '{cid}' nao existe. Opcoes: {cidades}")
        return
    dec = _eng().vpl_por_subbacia(cen, res)
    plano = res["plano"]; elig = res.get("elig") or {}
    subs = [sb for sb, n in cen.nos.items() if n.cidade == cid]
    pct = _cobertura_pct(cen, res).get(cid, [0.0] * cen.anos)
    det = [d for d in (res.get("metas_detalhe") or []) if d["sistema"] == cid]
    fe = (res.get("fator_esgoto_ano") or {}).get(cid) or []

    print("=" * 78)
    print(f"CIDADE {cid}")
    print("=" * 78)
    print(f"  sub-bacias           : {len(subs)}   |   sistemas: "
          f"{len({cen.nos[s].sistema for s in subs})}")
    print(f"  universo (regua {getattr(cen, 'unidade_cobertura', {}).get(cid, 'ligacoes')}): "
          f"{cen.max_lig.get(cid, 0):,.0f}   |   base atendida: {cen.base_lig.get(cid, 0):,.0f} "
          f"({cen.base_lig.get(cid, 0) / max(cen.max_lig.get(cid, 1), 1) * 100:.1f}%)")
    print(f"  cobertura no fim     : {pct[-1]:.1f}%")
    vpl = sum(dec.get(sb, {}).get("vpl", 0.0) for sb in subs)
    capex = sum(o.capex for oid, o in cen.obras.items()
                if o.eh_aegea() and plano.get(oid) is not None and cen.cidade_da(o) == cid)
    print(f"  VPL da cidade        : {_brl(vpl)}")
    print(f"  CAPEX construido     : {_brl(capex)}")
    if fe:
        print(f"  paridade esgoto/agua : {fe[0]:.2f} -> {fe[-1]:.2f}"
              f"{'   (sobe de faixa ao longo do plano)' if abs(fe[-1] - fe[0]) > 1e-9 else '   (constante)'}")
    peso = (getattr(cen, "peso_cidade", {}) or {}).get(cid, 1.0)
    if peso != 1.0:
        print(f"  peso na funcao objetivo: {peso}x")
    if det:
        print("\n  METAS DE COBERTURA:")
        for d in sorted(det, key=lambda x: x["ano"]):
            mk = "OK  " if d["atingida"] else "FALHA"
            print(f"    {d['ano']}  alvo {d['pct']*100:5.1f}%  "
                  f"({d['alvo']:,.0f} lig)   realizado {d['cobertura']:,.0f} lig   "
                  f"{mk}" + (f"   deficit {d['deficit']:,.0f} lig" if d["deficit"] > 1e-6 else ""))
    print("\n  TOP SUB-BACIAS POR VPL:")
    rk = sorted(((sb, dec.get(sb, {}).get("vpl", 0.0)) for sb in subs),
                key=lambda kv: kv[1], reverse=True)
    for sb, v in rk[:8]:
        c = next((c for c in cen.coletas if c.no == sb), None)
        fl = "fatura" if (c and elig.get(c.id)) else "nao fatura"
        print(f"    {sb:16} {_brl(v):>20}   {fl}")
    if len(rk) > 8:
        print(f"    ... e as {len(rk)-8} piores; a ultima e {rk[-1][0]} ({_brl(rk[-1][1])})")
    print("=" * 78)

    if not grafico:
        return
    yrs = _anos_calendario(cen)
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.4))
    fig.suptitle(f"Cidade {cid}", fontsize=13, weight="bold")

    a = ax[0]
    a.plot(yrs, pct, color=TEAL, lw=2.6, marker="o", ms=3)
    for d in det:
        a.scatter([d["ano"]], [d["pct"] * 100], color=(GREEN if d["atingida"] else RED),
                  s=60, marker="D", zorder=5)
        a.annotate(f"{d['pct']*100:.0f}%", (d["ano"], d["pct"] * 100),
                   textcoords="offset points", xytext=(5, 6), fontsize=8,
                   color=(GREEN if d["atingida"] else RED))
    a.set_ylim(0, 105); a.grid(alpha=.2); a.set_ylabel("cobertura (%)")
    a.set_title("Cobertura vs metas", weight="bold")

    a = ax[1]
    ct = defaultdict(float)
    for oid, o in cen.obras.items():
        if o.eh_aegea() and plano.get(oid) is not None and cen.cidade_da(o) == cid:
            ct[_elemento_nome(o)] += o.capex
    if ct:
        it = sorted(ct.items(), key=lambda kv: kv[1])
        a.barh([k for k, _ in it], [v / 1e6 for _, v in it],
               color=[_COR_ELEM.get(k, BLUE) for k, _ in it])
        for i, (k, v) in enumerate(it):
            a.text(v / 1e6, i, f"  {v/1e6:,.1f}", va="center", fontsize=9)
        a.margins(x=.2)
    a.set_xlabel("R$ milhoes"); a.grid(alpha=.2, axis="x")
    a.set_title("CAPEX por elemento", weight="bold")

    a = ax[2]
    vals = sorted((dec.get(sb, {}).get("vpl", 0.0) / 1e6 for sb in subs), reverse=True)
    a.bar(range(len(vals)), vals, color=[TEAL if v >= 0 else RED for v in vals])
    a.axhline(0, color=GREY, lw=1); a.grid(alpha=.2, axis="y")
    a.set_xlabel("sub-bacias (ordenadas)"); a.set_ylabel("R$ milhoes")
    a.set_title("VPL por sub-bacia", weight="bold")
    plt.tight_layout()
    return fig


# =============================================================================
#  5) COBERTURA AO LONGO DO TEMPO
# =============================================================================
def grafico_cobertura(cen, res, cidades=None, agregado=True, salvar=None):
    """Curva de cobertura por cidade (e agregada) com as metas marcadas."""
    anos = cen.anos
    yrs = _anos_calendario(cen)
    pct = _cobertura_pct(cen, res)
    todas = sorted(pct)
    sel = list(cidades) if cidades else todas
    sel = [c for c in sel if c in pct]
    det = res.get("metas_detalhe") or []

    fig, ax = plt.subplots(1, 2 if agregado else 1, figsize=(15, 5.2) if agregado else (9, 5.2))
    axes = ax if agregado else [ax]

    a = axes[0]
    cmap = plt.get_cmap("tab20")
    for i, c in enumerate(sel):
        a.plot(yrs, pct[c], lw=2.0, marker="o", ms=2.5, color=cmap(i % 20), label=c)
        md = [d for d in det if d["sistema"] == c]
        for d in md:
            a.scatter([d["ano"]], [d["pct"] * 100],
                      color=(GREEN if d["atingida"] else RED), s=55, marker="D",
                      zorder=5, edgecolors="white", linewidths=.8)
    ac = getattr(cen, "anos_capex", anos)
    a.axvline(_ano_base(cen) + ac - 1, color=GREY, ls="--", lw=1.2)
    a.text(_ano_base(cen) + ac - 1, 100.5, " fim da janela de CAPEX", fontsize=8, color=GREY)
    a.set_ylim(0, 105); a.grid(alpha=.2); a.set_ylabel("cobertura (%)")
    a.set_title("Cobertura por cidade  (losango = meta; verde atingida, vermelho nao)",
                weight="bold", fontsize=11)
    a.legend(fontsize=7, ncol=2, loc="lower right")

    if agregado:
        a = axes[1]
        cs = res.get("cobertura_sistema") or {}
        mx = sum((getattr(cen, "max_lig", {}) or {}).values()) or 1.0
        agg = [sum(cs.get(s, [0.0] * anos)[y] for s in cs) / mx * 100.0 for y in range(anos)]
        a.fill_between(yrs, agg, color=TEAL, alpha=.20)
        a.plot(yrs, agg, color=TEAL, lw=2.8, marker="o", ms=3, label="cobertura agregada")
        base = sum((getattr(cen, "base_lig", {}) or {}).values()) / mx * 100.0
        a.axhline(base, color=GREY, ls=":", lw=1.4)
        a.text(yrs[0], base + 1.5, f"ponto de partida {base:.1f}%", fontsize=8, color=GREY)
        a.annotate(f"{agg[-1]:.1f}%", (yrs[-1], agg[-1]), textcoords="offset points",
                   xytext=(-30, 8), fontsize=11, weight="bold", color=INK)
        ok = sum(1 for d in det if d["atingida"])
        a.set_ylim(0, 105); a.grid(alpha=.2); a.set_ylabel("cobertura (%)")
        a.set_title(f"Cobertura agregada da regional  ({ok}/{len(det)} metas atingidas)",
                    weight="bold", fontsize=11)
        a.legend(fontsize=9, loc="lower right")

    plt.tight_layout()
    if salvar:
        fig.savefig(salvar, dpi=120)
    return fig


# =============================================================================
#  MENU INTERATIVO (ipywidgets — funciona no Google Colab)
# =============================================================================
def dashboard(cen, res):
    """Monta o painel com abas e menus. Se ipywidgets nao existir, cai para o
    modo texto e imprime as instrucoes de uso das funcoes avulsas."""
    try:
        import ipywidgets as W
        from IPython.display import display, clear_output
    except Exception:
        print("ipywidgets indisponivel — use as funcoes diretamente:")
        print("  D.painel_geral(cen,res)         D.tabela_obras(cen,res,status='FORA')")
        print("  D.deep_dive_subbacia(cen,res,'<sub-bacia>')")
        print("  D.visao_cidade(cen,res,'<cidade>')   D.grafico_cobertura(cen,res)")
        return

    cidades = sorted({n.cidade for n in cen.nos.values()})
    subs = sorted(cen.nos)
    obras = sorted(cen.obras)

    # --- aba 1: geral
    o1 = W.Output()
    with o1:
        mostrar(kpis(cen, res), linhas=None)
        painel_geral(cen, res); plt.show()

    # --- aba 2: obras (entrou / saiu)
    o2 = W.Output()
    f_st = W.Dropdown(options=["(todos)", "CONSTRUIDA", "FORA", "TERCEIRO", "N/A"],
                      value="FORA", description="status:")
    f_cid = W.Dropdown(options=["(todas)"] + cidades, description="cidade:")
    f_ob = W.Combobox(options=obras, description="detalhar:", placeholder="id da obra")
    b2 = W.Button(description="atualizar", button_style="info")

    def _run2(_=None):
        with o2:
            clear_output()
            if f_ob.value and f_ob.value in cen.obras:
                explicar_obra(cen, res, f_ob.value)
                return
            df = tabela_obras(cen, res,
                              status=(None if f_st.value == "(todos)" else f_st.value),
                              cidade=(None if f_cid.value == "(todas)" else f_cid.value))
            print(f"{len(df)} obra(s)  |  CAPEX envolvido: {_brl(df.capex.sum())}\n")
            print("POR CATEGORIA:")
            for c, n in df.categoria.value_counts().items():
                print(f"  {n:5}x  {c}")
            print()
            mostrar(df[["obra", "tipo", "cidade", "status", "categoria",
                        "obrigatoria", "capex", "inicio", "motivo"]],
                    {"capex": "R$ {:,.0f}"}, linhas=25)
    b2.on_click(_run2)
    _run2()

    # --- aba 3: sub-bacia
    o3 = W.Output()
    f_sb = W.Dropdown(options=subs, description="sub-bacia:")
    b3 = W.Button(description="analisar", button_style="info")

    def _run3(_=None):
        with o3:
            clear_output()
            mostrar(ranking_subbacias(cen, res, n=10),
                    {c: "R$ {:,.0f}" for c in ("capex_rateado", "opex_rateado", "receita_dir",
                                               "receita_ind", "efeito_base", "vpl")})
            deep_dive_subbacia(cen, res, f_sb.value); plt.show()
            topologia_subbacia(cen, res, f_sb.value); plt.show()
    b3.on_click(_run3)
    _run3()

    # --- aba 4: cidade
    o4 = W.Output()
    f_c2 = W.Dropdown(options=cidades, description="cidade:")
    b4 = W.Button(description="analisar", button_style="info")

    def _run4(_=None):
        with o4:
            clear_output()
            mostrar(tabela_cidades(cen, res),
                    {"capex": "R$ {:,.0f}", "vpl": "R$ {:,.0f}", "lig_novas": "{:,.0f}",
                     "cob_base_%": "{:.1f}", "cob_final_%": "{:.1f}"}, linhas=None)
            visao_cidade(cen, res, f_c2.value); plt.show()
    b4.on_click(_run4)
    _run4()

    # --- aba 5: cobertura
    o5 = W.Output()
    f_c3 = W.SelectMultiple(options=cidades, value=tuple(cidades[:8]),
                            description="cidades:", rows=8)
    b5 = W.Button(description="plotar", button_style="info")

    def _run5(_=None):
        with o5:
            clear_output()
            grafico_cobertura(cen, res, cidades=list(f_c3.value)); plt.show()
    b5.on_click(_run5)
    _run5()

    tabs = W.Tab(children=[
        o1,
        W.VBox([W.HBox([f_st, f_cid, f_ob, b2]), o2]),
        W.VBox([W.HBox([f_sb, b3]), o3]),
        W.VBox([W.HBox([f_c2, b4]), o4]),
        W.VBox([W.HBox([f_c3, b5]), o5]),
    ])
    for i, t in enumerate(["1. Visao geral", "2. Obras: entrou/saiu", "3. Sub-bacia",
                           "4. Cidade", "5. Cobertura"]):
        tabs.set_title(i, t)
    display(tabs)
    return tabs
