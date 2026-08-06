# =============================================================================
#  PERSISTENCIA DO OTIMIZADOR DE CAPEX — ESGOTO
#  Materializa uma rodada (cen + res) num conjunto de TABELAS, para que todas as
#  visualizacoes possam ser reconstruidas SEM reexecutar o otimizador, e para que
#  a rodada possa ser reproduzida/auditada meses depois.
#
#  Uso tipico:
#      import persistencia as P
#      tabs = P.materializar(cen, res, banco=BANCO, params={...})
#      P.resumo(tabs)
#      P.salvar(tabs, 'resultados/')                       # local / Colab (parquet)
#      P.salvar(tabs, 'abfss://dados@conta.dfs.core.windows.net/otimizador/')
#      P.salvar_delta(tabs, 'catalogo.otimizador')          # Databricks (Delta)
#
#  Tabelas geradas (todas com run_id):
#      run_meta            1 linha  — parametros, versoes, status, totais
#      run_obra            1697     — atributos + decisao + motivo de cada obra
#      run_subbacia         484     — VPL decomposto e economia potencial
#      run_dependencia     ~5k      — arestas obra -> sub-bacia + fracao de rateio
#      run_subbacia_ano    ~6k      — receita/CAPEX/OPEX por sub-bacia e ano (curvas)
#      run_sistema          100     — ETE, capacidade, folga, ocupacao e horizonte
#      run_ano               24     — CAPEX/OPEX/receita vs teto por ano
#      run_mes              288     — CAPEX mensal (curva S / fluxo de caixa)
#      run_cidade            14     — consolidado por cidade
#      run_cidade_ano       336     — perfil de CAPEX por cidade e ano
#      run_cobertura        336     — cobertura por cidade e ano
#      run_meta_cobertura    31     — meta a meta: alvo, realizado, deficit
#      run_paridade         336     — paridade aplicada por cidade e ano
#      run_auditoria          n     — anos com estouro de teto e reparos
#      snapshot__<aba>        -     — copia das abas do banco de entrada
# =============================================================================
import datetime as _dt
import hashlib as _hl
import json as _js
import math as _math
import os as _os
import shutil as _shutil
import uuid as _uuid

import pandas as pd

_M = None
_D = None


def set_engine(engine, dashboard=None):
    """Registra o engine (otimizador_capex_vNN) e, opcionalmente, o dashboard
    (usado para os campos de motivo/categoria/elo e economia potencial)."""
    global _M, _D
    _M = engine
    if dashboard is not None:
        _D = dashboard
        try:
            dashboard.set_engine(engine)
        except Exception:
            pass
    return _M


def _eng():
    if _M is None:
        raise RuntimeError("engine nao registrado: chame persistencia.set_engine(M, D)")
    return _M


# ------------------------------------------------------------------ utilitarios
def novo_run_id(prefixo="run"):
    return f"{prefixo}_{_dt.datetime.now():%Y%m%d_%H%M%S}_{_uuid.uuid4().hex[:6]}"


def _md5(caminho):
    try:
        # `with`: este arquivo costuma ser o snapshot temporario que o job apaga logo
        # depois — handle pendurado impede a remocao no Windows.
        with open(caminho, "rb") as f:
            return _hl.md5(f.read()).hexdigest()
    except Exception:
        return None


def _ano_base(cen):
    ab = getattr(cen, "ano_base", None)
    return min(ab.values()) if ab else 2026


def _data(cen, m):
    if m is None:
        return None
    ab = _ano_base(cen)
    return f"{ab + m // 12:04d}-{(m % 12) + 1:02d}"


def _j(x):
    try:
        return _js.dumps(x, ensure_ascii=False, default=str)
    except Exception:
        return None


# =============================================================================
#  MATERIALIZACAO
# =============================================================================
def materializar(cen, res, banco=None, params=None, run_id=None, incluir_snapshot=True,
                 economia=True, arquivo_fonte=None):
    """cen + res -> dict {nome_tabela: DataFrame}. Nao escreve nada em disco.

    `banco` e o ROTULO da origem, gravado em run_meta.banco_arquivo. No caminho Excel ele
    e o proprio caminho do arquivo; no caminho Postgres e algo como 'postgres://input',
    que descreve a origem mas nao existe em disco.

    `arquivo_fonte` (opcional) e o ARQUIVO de onde tirar o snapshot__* e o banco_md5,
    quando ele nao coincide com o rotulo. E o que permite o job do Postgres preservar a
    copia congelada do cadastro — sem isso, `_os.path.exists('postgres://input')` e falso
    e a rodada sai sem snapshot nenhum, quebrando a reproducao/auditoria.
    """
    fonte = arquivo_fonte or banco
    M = _eng()
    rid = run_id or novo_run_id()
    reg = list(cen.regionais)[0]
    anos = cen.anos
    ac = int(getattr(cen, "anos_capex", anos))
    ab = _ano_base(cen)
    plano = res.get("plano", {}) or {}
    elig = res.get("elig", {}) or {}
    ready = res.get("ready", {}) or {}
    inif = res.get("inicio_fat", {}) or {}
    dec = M.vpl_por_subbacia(cen, res)
    fat = res.get("fator_esgoto_ano", {}) or {}
    aud = res.get("auditoria_orcamento", {}) or {}
    T = {}

    # ---------------------------------------------------------------- run_meta
    T["run_meta"] = pd.DataFrame([{
        "run_id": rid,
        "data_hora": _dt.datetime.now().isoformat(timespec="seconds"),
        "engine": getattr(M, "__name__", None),
        "engine_arquivo": getattr(M, "__file__", None),
        "engine_md5": _md5(getattr(M, "__file__", "") or ""),
        "banco_arquivo": banco,
        "banco_md5": _md5(fonte) if fonte else None,
        "regional": reg,
        "anos_horizonte": anos,
        "anos_capex": ac,
        "ano_base": ab,
        "ete_faseada": bool(getattr(cen, "ete_faseada", False)),
        "curva_adocao": getattr(cen, "curva_adocao", None),
        "foco_cobertura": getattr(cen, "foco_cobertura", None),
        "penalidade_cobertura": getattr(cen, "penalidade_cobertura", None),
        "peso_cobertura": getattr(cen, "peso_cobertura", None),
        "peso_cidade": _j(getattr(cen, "peso_cidade", {}) or {}),
        "orcamento_por_ano": _j({ab + y: cen.orc[reg][y] for y in range(min(ac, len(cen.orc[reg])))}),
        "orcamento_total": sum(cen.orc[reg][y] for y in range(min(ac, len(cen.orc[reg])))),
        "params_extra": _j(params or {}),
        "milp_status": res.get("milp_status"),
        "milp_solver": res.get("milp_solver"),
        "milp_bound": res.get("milp_bound"),
        "vpl": res.get("vpl"),
        "vpl_obj": res.get("vpl_obj"),
        "vp_efeito_base": res.get("vp_efeito_base"),
        "capex_total": sum(res["capex_ano"][reg]),
        "opex_total": sum(res["opex_ano"]),
        "receita_total": sum(res.get("receita_ano") or []),
        "obras_total": len(cen.obras),
        "obras_construidas": sum(1 for oid, y in plano.items()
                                 if y is not None and cen.obras[oid].eh_aegea()),
        "obrig_total": res.get("obrig_total"),
        "obrig_construidas": res.get("obrig_construidas"),
        "obrig_desconsideradas": _j(res.get("obrig_desconsideradas_fora_janela") or []),
        "subbacias_total": len(cen.nos),
        "subbacias_faturando": sum(1 for c in cen.coletas if elig.get(c.id)),
        "metas_total": len(res.get("metas_detalhe") or []),
        "metas_nao_atingidas": res.get("metas_nao_atingidas"),
        "deficit_cobertura": res.get("deficit_cobertura"),
        "auditoria_ok": aud.get("ok", True),
        "auditoria_reparos": len(aud.get("reparos") or []),
        "aviso_orcamento": res.get("aviso_orcamento"),
        "aviso_obrigatoria": res.get("aviso_obrigatoria"),
    }])

    # ------------------------------------------------------------- run_obra
    motivos = {}
    if _D is not None:
        try:
            _df = _D.tabela_obras(cen, res)
            motivos = {r["obra"]: r for _, r in _df.iterrows()}
        except Exception:
            motivos = {}
    lin = []
    for oid, o in cen.obras.items():
        y = plano.get(oid)
        mv = motivos.get(oid, {})
        _pref = str(oid).split("_")[0].lower()
        _comp = ({"lig": "lig", "rede": "rede", "tro": "tro", "eee": "eee", "lr": "lr", "cts": "cts"}
                 .get(_pref, ("ete_mod" if o.tipo == "ete_mod" else
                              ("ete" if o.tipo == "ete" else o.tipo))))
        lin.append({
            "run_id": rid, "obra_id": oid, "tipo": o.tipo, "componente": _comp,
            "no": o.no, "sistema": getattr(o, "sistema", None),
            "is_cts": bool(getattr(cen.nos.get(o.no), "is_cts", False)),
            "cidade": cen.cidade_da(o), "regional": cen.regional_da(o),
            "responsavel": o.responsavel, "necessaria": bool(o.necessaria),
            "capex": o.capex, "capex_componentes": _j(o.capex_comp),
            "quantidade": getattr(o, "quantidade", None),
            "unidade": getattr(o, "unidade", None),
            "preco_unitario": getattr(o, "preco_unitario", None),
            "opex_ano": o.opex_ano, "prazo_meses": o.prazo,
            "prazo_inicio_meses": o.prazo_inicio, "inicio_min_mes": o.inicio_min,
            "obrigatoria": bool(o.obrigatoria), "obrig_ano_plano": getattr(o, "_obrig_planyear", None),
            "proibida_ate": o.proibida_ate, "proibida_nunca": bool(getattr(o, "proibida_nunca", False)),
            "ligacoes": o.lig, "ticket_mes": o.ticket_mes, "preco_ligacao": o.preco_ligacao,
            "arrec_dir": o.arrec_dir, "arrec_ind": o.arrec_ind,
            "lag_meses": o.lag, "maturacao_meses": o.mat, "wacc": o.wacc, "wacc_origem": getattr(o,"wacc_origem","proprio"),
            "mes_inicio": y, "data_inicio": _data(cen, y),
            "mes_pronta": ready.get(oid), "data_pronta": _data(cen, ready.get(oid)),
            "construida": y is not None,
            "faturando": bool(elig.get(oid)) if o.tipo == "coleta" else None,
            "mes_inicio_faturamento": inif.get(oid), "data_inicio_faturamento": _data(cen, inif.get(oid)),
            "status": mv.get("status"), "categoria_motivo": mv.get("categoria"),
            "motivo": mv.get("motivo"), "elo_que_trava": mv.get("elo_que_trava") or None,
            "saldo_potencial": mv.get("saldo_potencial"),
        })
    T["run_obra"] = pd.DataFrame(lin)

    # ---------------------------------------------------------- run_subbacia
    lin = []
    for sb, n in cen.nos.items():
        c = next((c for c in cen.coletas if c.no == sb), None)
        d = dec.get(sb, {})
        sr = (getattr(cen, "sub_receita", {}) or {}).get(sb, {})
        ec = {}
        if economia and _D is not None and c is not None and not elig.get(c.id):
            try:
                ec = _D.economia_potencial(cen, res, sb) or {}
            except Exception:
                ec = {}
        lin.append({
            "run_id": rid, "sub_bacia": sb, "cidade": n.cidade, "sistema": n.sistema,
            "regional": n.regional, "jusante": n.jusante,
            "is_cts": bool(getattr(n, "is_cts", False)),
            "tipo_estrutura": ("CTS" if getattr(n, "is_cts", False) else "sub_bacia"),
            "vazao_marginal": cen.vazao.get(sb, 0.0),
            "unid_fator_cobertura": (getattr(cen, "unid_fator", {}) or {}).get(sb, 1.0),
            "ligacoes_atuais": sr.get("atuais"), "ticket_medio": sr.get("ticket"),
            "arrecadacao": sr.get("arrec"),
            "ligacoes_novas": (c.lig if c else None),
            "obra_coleta": (c.id if c else None),
            "faturando": bool(c is not None and elig.get(c.id)),
            "mes_inicio_faturamento": (inif.get(c.id) if c else None),
            "data_inicio_faturamento": _data(cen, inif.get(c.id) if c else None),
            "motivo_sem_receita": (res.get("motivo") or {}).get(c.id) if c else None,
            "vpl": d.get("vpl"), "vp_capex_rateado": d.get("capex"), "vp_opex_rateado": d.get("opex"),
            "vp_receita_direta": d.get("rec_dir"), "vp_receita_indireta": d.get("rec_ind"),
            "vp_efeito_base": d.get("efeito_base"),
            "pot_vp_receita": ec.get("vp_receita"), "pot_vp_capex_solo": ec.get("vp_capex_solo"),
            "pot_vp_capex_rateado": ec.get("vp_capex_rateado"), "pot_vp_opex": ec.get("vp_opex"),
            "pot_saldo_solo": ec.get("saldo_solo"), "pot_saldo_rateado": ec.get("saldo_rateado"),
            "pot_obras_faltantes": ec.get("obras_faltantes"),
            # densidades DERIVADAS da base comercial (nao sao input)
            "densidade_economias": ((getattr(cen, "densidade", {}) or {}).get(sb, {}) or {}).get("economias"),
            "densidade_populacao": ((getattr(cen, "densidade", {}) or {}).get(sb, {}) or {}).get("populacao"),
            "unidade_cobertura": (getattr(cen, "unidade_cobertura", {}) or {}).get(n.cidade),
            "fator_unidade_cobertura": (getattr(cen, "unid_fator", {}) or {}).get(sb, 1.0),
            "potencial_crescimento": (getattr(cen, "potencial_crescimento", {}) or {}).get(sb, 1.0),
            "wacc_receita": (M._wacc_receita(cen, c) if c is not None else None),
            "horizonte_anos": int(cen.hz.get(n.sistema, cen.anos)),
            # colunas reservadas para o mapa — preencher quando o banco tiver geo
            "latitude": None, "longitude": None,
        })
    T["run_subbacia"] = pd.DataFrame(lin)

    # -------------------------------------------------------- run_dependencia
    req_sb = _D._mapa_exigencias(cen) if _D is not None else _mapa_exigencias_local(cen)
    lin = []
    for oid, subs in req_sb.items():
        o = cen.obras.get(oid)
        if o is None:
            continue
        subs = [s for s in subs if s in cen.nos]
        tot = sum(cen.vazao.get(s, 0.0) for s in subs)
        for s in subs:
            fr = (cen.vazao.get(s, 0.0) / tot) if tot > 0 else (1.0 / max(len(subs), 1))
            c = next((c for c in cen.coletas if c.no == s), None)
            lin.append({
                "run_id": rid, "obra_id": oid, "obra_tipo": o.tipo, "sub_bacia": s,
                "cidade": cen.nos[s].cidade, "sistema": cen.nos[s].sistema,
                "vazao_sub_bacia": cen.vazao.get(s, 0.0), "vazao_total_obra": tot,
                "fracao_rateio": fr, "capex_rateado": o.capex * fr,
                "n_dependentes": len(subs),
                "obra_construida": plano.get(oid) is not None,
                "sub_bacia_faturando": bool(c is not None and elig.get(c.id)),
            })
    T["run_dependencia"] = pd.DataFrame(lin)

    # ----------------------------------------------------- run_subbacia_ano
    T["run_subbacia_ano"] = _serie_subbacia_ano(cen, res, rid, req_sb)
    T["run_sistema"] = _tabela_sistema(cen, res, rid)

    # -------------------------------------------------------------- run_ano
    g = res["capex_ano"][reg]; t = cen.orc[reg]
    op = res["opex_ano"]; rc = res.get("receita_ano") or [0.0] * anos
    # efeito-base NOMINAL por ano: usa o do engine (v51+) se houver; senao calcula do cen
    # (assim a persistencia nao depende da versao do engine para o EBITDA).
    eb = res.get("efeito_base_ano") or _efeito_base_ano_cen(cen, res, anos)
    ebt = res.get("ebitda_ano")
    lin = []
    acum_eb = 0.0
    for y in range(anos):
        teto = t[y] if y < len(t) else 0.0
        rec_nov = rc[y] if y < len(rc) else 0.0
        ef = eb[y] if y < len(eb) else 0.0
        rec_total = rec_nov + ef
        ebitda = ebt[y] if (ebt and y < len(ebt)) else (rec_total - op[y])
        acum_eb += ebitda
        lin.append({"run_id": rid, "ano": ab + y, "ano_indice": y,
                    "capex": g[y], "opex": op[y],
                    "receita": rec_nov, "receita_efeito_base": ef, "receita_total": rec_total,
                    "ebitda": ebitda, "ebitda_acumulado": acum_eb,
                    "ebitda_margem_pct": (ebitda / rec_total * 100.0 if rec_total else None),
                    "teto_capex": teto, "uso_teto_pct": (g[y] / teto * 100.0 if teto else None),
                    "excesso": max(0.0, g[y] - teto), "dentro_janela_capex": y < ac})
    T["run_ano"] = pd.DataFrame(lin)

    # -------------------------------------------------------------- run_mes
    capm = {}
    for oid, y in plano.items():
        if y is None:
            continue
        o = cen.obras[oid]
        if not o.eh_aegea() or o.capex <= 0:
            continue
        pe = int(o.prazo or 0)
        if pe > 0:
            for m in range(y, y + pe):
                capm[m] = capm.get(m, 0.0) + o.capex / pe
        else:
            capm[y] = capm.get(y, 0.0) + o.capex
    lin = []
    acum = 0.0
    for m in range(anos * 12):
        v = capm.get(m, 0.0); acum += v
        lin.append({"run_id": rid, "mes_indice": m, "ano": ab + m // 12,
                    "mes": (m % 12) + 1, "competencia": _data(cen, m),
                    "capex_mes": v, "capex_acumulado": acum})
    T["run_mes"] = pd.DataFrame(lin)

    # ------------------------------------------------- run_cidade / cidade_ano
    cobs = res.get("cobertura_sistema", {}) or {}
    mx = getattr(cen, "max_lig", {}) or {}
    bs = getattr(cen, "base_lig", {}) or {}
    det = res.get("metas_detalhe") or []
    lin = []; lin_ca = []; lin_cob = []; lin_par = []
    for cid in sorted({n.cidade for n in cen.nos.values()}):
        subs = [s for s, n in cen.nos.items() if n.cidade == cid]
        obras_cid = [(oid, o) for oid, o in cen.obras.items() if cen.cidade_da(o) == cid]
        md = [d for d in det if d["sistema"] == cid]
        fe = fat.get(cid) or []
        cap_ano = [0.0] * anos
        for oid, o in obras_cid:
            y = plano.get(oid)
            if y is None or not o.eh_aegea():
                continue
            pe = int(o.prazo or 0)
            if pe > 0:
                for m in range(y, y + pe):
                    if m // 12 < anos:
                        cap_ano[m // 12] += o.capex / pe
            elif y // 12 < anos:
                cap_ano[y // 12] += o.capex
        lin.append({
            "run_id": rid, "cidade": cid, "sub_bacias": len(subs),
            "obras_feitas": sum(1 for oid, o in obras_cid
                                if o.necessaria and o.eh_aegea() and plano.get(oid) is not None),
            "obras_fora": sum(1 for oid, o in obras_cid
                              if o.necessaria and o.eh_aegea() and plano.get(oid) is None),
            "capex_total": sum(cap_ano),
            "vpl": sum(dec.get(s, {}).get("vpl", 0.0) for s in subs),
            "ligacoes_novas": sum(c.lig for c in cen.coletas
                                  if cen.cidade_da(c) == cid and elig.get(c.id)),
            "universo": mx.get(cid), "base_atendida": bs.get(cid),
            "cobertura_base_pct": (bs.get(cid, 0.0) / mx[cid] * 100.0) if mx.get(cid) else None,
            "cobertura_final_pct": ((cobs.get(cid, [0.0])[-1] / mx[cid] * 100.0)
                                    if mx.get(cid) and cobs.get(cid) else None),
            "metas_total": len(md), "metas_atingidas": sum(1 for d in md if d["atingida"]),
            "paridade_inicial": (fe[0] if fe else None), "paridade_final": (fe[-1] if fe else None),
            "peso_cidade": (getattr(cen, "peso_cidade", {}) or {}).get(cid, 1.0),
            "unidade_cobertura": (getattr(cen, "unidade_cobertura", {}) or {}).get(cid),
        })
        for y in range(anos):
            lin_ca.append({"run_id": rid, "cidade": cid, "ano": ab + y, "capex": cap_ano[y]})
            serie = cobs.get(cid) or []
            cv = serie[y] if y < len(serie) else None
            lin_cob.append({"run_id": rid, "cidade": cid, "ano": ab + y,
                            "ligacoes_cobertas": cv, "universo": mx.get(cid),
                            "cobertura_pct": (cv / mx[cid] * 100.0) if (mx.get(cid) and cv is not None) else None})
            if fe:
                p0 = fe[0]; pv = fe[y] if y < len(fe) else fe[-1]
                lin_par.append({"run_id": rid, "cidade": cid, "ano": ab + y,
                                "paridade": pv, "paridade_base": p0, "delta_paridade": pv - p0})
    T["run_cidade"] = pd.DataFrame(lin)
    T["run_cidade_ano"] = pd.DataFrame(lin_ca)
    T["run_cobertura"] = pd.DataFrame(lin_cob)
    T["run_paridade"] = pd.DataFrame(lin_par)

    # ------------------------------------------------------ run_meta_cobertura
    lin = [{"run_id": rid, "cidade": d["sistema"], "ano": d["ano"], "pct_alvo": d["pct"],
            "alvo_ligacoes": d["alvo"], "cobertura_ligacoes": d["cobertura"],
            "deficit_ligacoes": d["deficit"], "atingida": d["atingida"],
            "dentro_janela_capex": True} for d in det]
    # metas do contrato que a janela de CAPEX NAO alcanca: ficam de fora da otimizacao,
    # mas precisam aparecer — senao "100% das metas" engana quem le.
    _vistas = {(d["sistema"], d["ano"]) for d in det}
    for _cid, _al in (getattr(cen, "metas_cobertura", {}) or {}).items():
        for _ano, _pct in _al.items():
            if (_cid, int(_ano)) in _vistas:
                continue
            lin.append({"run_id": rid, "cidade": _cid, "ano": int(_ano), "pct_alvo": _pct,
                        "alvo_ligacoes": _pct * (getattr(cen, "max_lig", {}) or {}).get(_cid, 0.0),
                        "cobertura_ligacoes": None, "deficit_ligacoes": None,
                        "atingida": None, "dentro_janela_capex": False})
    T["run_meta_cobertura"] = pd.DataFrame(lin) if lin else pd.DataFrame(
        columns=["run_id", "cidade", "ano", "pct_alvo", "alvo_ligacoes", "cobertura_ligacoes",
                 "deficit_ligacoes", "atingida", "dentro_janela_capex"])

    # ----------------------------------------------------------- run_auditoria
    lin = [{"run_id": rid, "tipo": "violacao", "ano": a, "gasto": gs, "teto": tt,
            "excesso": ex, "detalhe": None}
           for (a, _iy, gs, tt, ex) in (aud.get("violacoes") or [])]
    lin += [{"run_id": rid, "tipo": "reparo", "ano": None, "gasto": None, "teto": None,
             "excesso": None, "detalhe": _j(r)} for r in (aud.get("reparos") or [])]
    T["run_auditoria"] = pd.DataFrame(lin) if lin else pd.DataFrame(
        columns=["run_id", "tipo", "ano", "gasto", "teto", "excesso", "detalhe"])

    # -------------------------------------------------------------- snapshot
    if incluir_snapshot and fonte and _os.path.exists(fonte):
        for aba, df in snapshot_banco(fonte, rid).items():
            T[aba] = df
    return T


def _tabela_sistema(cen, res, rid):
    """Um retrato por SISTEMA: horizonte, ETE, capacidade, folga e ocupacao.
    Sem isto nao da para analisar gargalo de tratamento — que e metade do modelo."""
    plano = res.get("plano", {}) or {}
    elig = res.get("elig", {}) or {}
    ab = _ano_base(cen)
    lin = []
    for sis in sorted(cen.sistemas):
        e = cen.ete_do_sistema.get(sis)
        mods = list((getattr(cen, "modulos_sis", {}) or {}).get(sis, []))
        subs = [sb for sb, n in cen.nos.items() if n.sistema == sis]
        cid = cen.nos[subs[0]].cidade if subs else None
        constr = [m for m in mods if plano.get(m.id) is not None]
        vaz_con = sum(cen.vazao.get(sb, 0.0) for sb in subs
                      if any(c.no == sb and elig.get(c.id) for c in cen.coletas))
        vaz_tot = sum(cen.vazao.get(sb, 0.0) for sb in subs)
        folga = float(getattr(e, "folga", 0.0) or 0.0) if e else 0.0
        capmod = float(getattr(e, "cap_modulo", 0.0) or 0.0) if e else 0.0
        cap_inst = folga + len(constr) * capmod
        lin.append({
            "run_id": rid, "sistema": sis, "cidade": cid,
            "horizonte_anos": int(cen.hz.get(sis, cen.anos)),
            "ano_fim_concessao": ab + int(cen.hz.get(sis, cen.anos)) - 1,
            "sub_bacias": len(subs), "sub_bacias_faturando":
                sum(1 for sb in subs if any(c.no == sb and elig.get(c.id) for c in cen.coletas)),
            "ete_id": (e.id if e else None),
            "ete_nova": bool(getattr(e, "nova", False)) if e else None,
            "ete_responsavel": (e.responsavel if e else None),
            "folga_inicial": folga, "capacidade_modulo": capmod,
            "capex_modulo": float(getattr(e, "capex_modulo", 0.0) or 0.0) if e else None,
            "capex_terreno": float(getattr(e, "capex_terreno", 0.0) or 0.0) if e else None,
            "modulos_disponiveis": len(mods), "modulos_construidos": len(constr),
            "capex_modulos_construidos": sum(m.capex for m in constr),
            "capacidade_instalada": cap_inst,
            "vazao_conectada": vaz_con, "vazao_total_sistema": vaz_tot,
            "ocupacao_pct": (vaz_con / cap_inst * 100.0) if cap_inst > 0 else None,
            "folga_remanescente": max(0.0, cap_inst - vaz_con),
            "vazao_nao_atendida": max(0.0, vaz_tot - vaz_con),
            "primeiro_modulo_mes": min((plano[m.id] for m in constr), default=None),
        })
    return pd.DataFrame(lin)


def _efeito_base_ano_cen(cen, res, anos):
    """Efeito-base NOMINAL por ano da UNIDADE, calculado direto do cenario (independe do engine).
    Some, por sistema, base_ano * (paridade(ano) - paridade inicial), ate o fim da concessao."""
    M = _eng()
    fatc = res.get("fator_esgoto_ano", {}) or {}
    sr = getattr(cen, "sub_receita", {}) or {}
    fall = getattr(cen, "fator_esgoto", {}) or {}
    maxl = getattr(cen, "max_lig", {}) or {}; basel = getattr(cen, "base_lig", {}) or {}
    hz = getattr(cen, "hz", {}) or {}
    out = [0.0] * anos
    f0 = {}
    for cid, fx in fall.items():
        fx = sorted(fx); mx = float(maxl.get(cid, 0.0) or 0.0); bs = float(basel.get(cid, 0.0) or 0.0)
        f0[cid] = M._faixa_fator(fx, (bs / mx) if mx > 0 else 0.0)
    for sb, n in cen.nos.items():
        fe = fatc.get(n.cidade); d = sr.get(sb)
        if not fe or not d:
            continue
        base_ano = (float(d.get("atuais", 0.0) or 0.0) * float(d.get("ticket", 0.0) or 0.0)
                    * 12.0 * float(d.get("arrec", 1.0) or 1.0))
        if base_ano <= 0:
            continue
        fr0 = f0.get(n.cidade, 1.0)
        H = min(len(fe), int(hz.get(n.sistema, anos)), anos)
        for Y in range(H):
            out[Y] += base_ano * (fe[Y] - fr0)
    return out


def _serie_subbacia_ano(cen, res, rid, req_sb):
    """Serie ANUAL por sub-bacia: receita direta/indireta (com rampa e paridade),
    CAPEX e OPEX rateados por vazao. E o que sustenta a curva de receita do deep dive
    e qualquer cascata reconstruida no front. A soma por ano reproduz run_ano."""
    M = _eng()
    anos = cen.anos
    ab = _ano_base(cen)
    plano = res.get("plano", {}) or {}
    elig = res.get("elig", {}) or {}
    inif = res.get("inicio_fat", {}) or {}
    opini = res.get("opex_ini", {}) or {}
    fatc = res.get("fator_esgoto_ano", {}) or {}
    rec_d = {sb: [0.0] * anos for sb in cen.nos}
    rec_i = {sb: [0.0] * anos for sb in cen.nos}
    cap = {sb: [0.0] * anos for sb in cen.nos}
    ope = {sb: [0.0] * anos for sb in cen.nos}
    efb = {sb: [0.0] * anos for sb in cen.nos}
    ativo = {sb: [False] * anos for sb in cen.nos}

    # ---- efeito-base NOMINAL por sub-bacia e ano (reajuste da paridade na base existente) ----
    sr = getattr(cen, "sub_receita", {}) or {}
    fall = getattr(cen, "fator_esgoto", {}) or {}
    maxl = getattr(cen, "max_lig", {}) or {}; basel = getattr(cen, "base_lig", {}) or {}
    f0 = {}
    for cid, fx in fall.items():
        fx = sorted(fx); mx = float(maxl.get(cid, 0.0) or 0.0); bs = float(basel.get(cid, 0.0) or 0.0)
        f0[cid] = M._faixa_fator(fx, (bs / mx) if mx > 0 else 0.0)
    for sb, n in cen.nos.items():
        fe = fatc.get(n.cidade); d = sr.get(sb)
        if not fe or not d:
            continue
        base_ano = (float(d.get("atuais", 0.0) or 0.0) * float(d.get("ticket", 0.0) or 0.0)
                    * 12.0 * float(d.get("arrec", 1.0) or 1.0))
        if base_ano <= 0:
            continue
        fr0 = f0.get(n.cidade, 1.0)
        H = min(len(fe), int((getattr(cen, "hz", {}) or {}).get(n.sistema, anos)), anos)
        for Y in range(H):                                # so ate o fim da concessao do sistema
            efb[sb][Y] += base_ano * (fe[Y] - fr0)

    # ---- receita (mesma mecanica de avaliar: rampa mensal + paridade do ano) ----
    for o in cen.coletas:
        if not elig.get(o.id) or o.no not in rec_d:
            continue
        ini = inif.get(o.id)
        if ini is None:
            continue
        Hm = cen.horizonte(o) * 12
        rdm = o.receita_dir_regime() / 12.0
        ri = o.receita_ind_total()
        fe = fatc.get(cen.cidade_da(o))
        nf = len(fe) if fe else 0
        for m in range(ini, Hm):
            Y = m // 12
            if Y >= anos:
                break
            k = m - ini
            f = (fe[Y] if (fe and Y < nf) else (fe[-1] if fe else 1.0))
            rec_d[o.no][Y] += rdm * M._rampa(k, o.mat) * f
            rec_i[o.no][Y] += ri * max(0.0, M._rampa(k, o.mat) - M._rampa(k - 1, o.mat))
            ativo[o.no][Y] = True

    # ---- CAPEX e OPEX rateados pelas MESMAS fracoes de vazao ----
    for oid, o in cen.obras.items():
        if not o.necessaria:
            continue
        subs = [x for x in req_sb.get(oid, []) if x in cap]
        if not subs:
            continue
        tot = sum(cen.vazao.get(x, 0.0) for x in subs)
        fr = {x: ((cen.vazao.get(x, 0.0) / tot) if tot > 0 else 1.0 / len(subs)) for x in subs}
        y = plano.get(oid)
        if o.eh_aegea() and y is not None and o.capex > 0:
            pe = int(o.prazo or 0)
            if pe > 0:
                for m in range(y, y + pe):
                    if m // 12 < anos:
                        for x in subs:
                            cap[x][m // 12] += (o.capex / pe) * fr[x]
            elif y // 12 < anos:
                for x in subs:
                    cap[x][y // 12] += o.capex * fr[x]
        st = opini.get(oid)
        if o.opex_ano > 0 and st is not None:
            Hm = cen.horizonte(o) * 12
            for m in range(st, Hm):
                if m // 12 < anos:
                    for x in subs:
                        ope[x][m // 12] += (o.opex_ano / 12.0) * fr[x]

    lin = []
    for sb, n in cen.nos.items():
        for Y in range(anos):
            if not (rec_d[sb][Y] or rec_i[sb][Y] or cap[sb][Y] or ope[sb][Y] or efb[sb][Y]):
                continue                                  # nao grava ano vazio
            rop = rec_d[sb][Y] + rec_i[sb][Y] + efb[sb][Y]     # receita operacional
            lin.append({"run_id": rid, "sub_bacia": sb, "cidade": n.cidade,
                        "sistema": n.sistema, "ano": ab + Y,
                        "receita_direta": rec_d[sb][Y], "receita_indireta": rec_i[sb][Y],
                        "efeito_base": efb[sb][Y],
                        "capex_rateado": cap[sb][Y], "opex_rateado": ope[sb][Y],
                        "ebitda": rop - ope[sb][Y],
                        "faturando": ativo[sb][Y]})
    return pd.DataFrame(lin) if lin else pd.DataFrame(
        columns=["run_id", "sub_bacia", "cidade", "sistema", "ano", "receita_direta",
                 "receita_indireta", "capex_rateado", "opex_rateado", "faturando"])


def _mapa_exigencias_local(cen):
    """Fallback do mapa obra -> sub-bacias quando o dashboard nao esta carregado."""
    M = _eng()
    from collections import defaultdict
    by_rede = defaultdict(list); by_transp = defaultdict(list)
    for q in cen.obras.values():
        if q.tipo == "rede":
            by_rede[q.no].append(q.id)
        elif q.tipo == "transporte":
            by_transp[q.no].append(q.id)
    req = defaultdict(list)
    for c in cen.coletas:
        X = c.no
        ids = [c.id] + by_rede.get(X, [])
        for n in M.caminho(cen, X):
            ids += by_transp.get(n, [])
        sis = cen.nos[X].sistema
        if sis in cen.ete_do_sistema:
            ids.append(cen.ete_do_sistema[sis].id)
        for rid_ in ids:
            req[rid_].append(X)
    sys_sub = defaultdict(list)
    for sb, no in cen.nos.items():
        sys_sub[no.sistema].append(sb)
    for sis, mods in (getattr(cen, "modulos_sis", {}) or {}).items():
        for m in mods:
            req[m.id] = list(sys_sub.get(sis, []))
    return dict(req)


def snapshot_banco(caminho, run_id):
    """Copia cada aba do banco de entrada como tabela 'snapshot__<aba>'."""
    out = {}
    try:
        # `with`: sem fechar, o handle fica aberto e no Windows o arquivo fica TRAVADO —
        # quem tenta apagar o xlsx temporario depois leva PermissionError (subclasse de
        # OSError) e o arquivo vaza em silencio.
        with pd.ExcelFile(caminho) as xl:
            for aba in xl.sheet_names:
                nome = "snapshot__" + str(aba).strip().lower().replace(" ", "_").replace("-", "_")
                df = xl.parse(aba)
                df.insert(0, "run_id", run_id)
                out[nome] = df
    except Exception as e:
        print(f"  [aviso] nao consegui abrir o banco para snapshot: {e}")
    return out


# =============================================================================
#  ESCRITA
# =============================================================================
def resumo(tabs, detalhe=True):
    """Imprime o que foi materializado."""
    tot = 0
    print(f"{'tabela':<28}{'linhas':>10}{'colunas':>10}")
    print("-" * 48)
    for k in sorted(tabs):
        df = tabs[k]; tot += len(df)
        if detalhe or not k.startswith("snapshot__"):
            print(f"{k:<28}{len(df):>10,}{len(df.columns):>10}")
    print("-" * 48)
    print(f"{'TOTAL':<28}{tot:>10,}")
    rid = tabs["run_meta"]["run_id"].iloc[0] if "run_meta" in tabs else "?"
    print(f"\nrun_id: {rid}")
    return tot


def _spark():
    try:
        from pyspark.sql import SparkSession
        return SparkSession.getActiveSession()
    except Exception:
        return None


def _rodadas_no_df(df, padrao):
    """Os `run_id` presentes no df — as particoes que esta gravacao substitui.

    Sai do PROPRIO df, e nao do `run_meta` do conjunto, porque e o dado gravado que
    determina em qual particao ele cai: usar um id de outra origem apagaria a
    particao errada (ou nenhuma).
    """
    if "run_id" not in df.columns or len(df) == 0:
        return [str(padrao)]
    vistos = [str(v) for v in pd.unique(df["run_id"].dropna())]
    return vistos or [str(padrao)]


def _apagar_particoes_spark(sp, base, rids):
    """Apaga `<base>/run_id=<rid>/` no storage do Spark (ADLS, DBFS, S3, local).

    E o equivalente do `DELETE FROM ... WHERE run_id = %s` que a publicacao no
    Postgres faz antes de inserir. Sem ele, `mode("append")` acrescenta arquivos
    NOVOS dentro da particao — e reexecutar a mesma rodada duplicaria o parquet em
    vez de substitui-lo. Apagar diretorio so vale para formato de arquivo; num Delta
    isso corromperia o log da tabela, e por isso o Delta tem caminho proprio.
    """
    jvm = sp._jvm
    conf = sp._jsc.hadoopConfiguration()
    for rid in rids:
        p = jvm.org.apache.hadoop.fs.Path(f"{base}/run_id={rid}")
        fs = p.getFileSystem(conf)
        if fs.exists(p):
            fs.delete(p, True)


def _tabela_existe(sp, alvo):
    """A tabela Delta gerenciada ja existe? Na duvida diz que NAO, e o caminho de
    criacao (append puro) e escolhido — errar para o outro lado faria a primeira
    gravacao falhar num `replaceWhere` sobre tabela inexistente."""
    try:
        return bool(sp.catalog.tableExists(alvo))
    except Exception:
        return False


def _delta_existe(sp, base):
    """Ha um Delta gravado em `base`? Decide entre criar e substituir a particao."""
    try:
        jvm = sp._jvm
        p = jvm.org.apache.hadoop.fs.Path(f"{str(base).rstrip('/')}/_delta_log")
        return p.getFileSystem(sp._jsc.hadoopConfiguration()).exists(p)
    except Exception:
        return False


def salvar(tabs, destino, formato="parquet", particionar_por_run=True, verbose=True):
    """Grava as tabelas em `destino`, uma pasta por tabela.

    destino pode ser:
      • caminho local ou do Colab   -> 'resultados/'
      • DBFS                        -> '/dbfs/mnt/otimizador/'
      • ADLS Gen2 / Blob (Spark)    -> 'abfss://dados@conta.dfs.core.windows.net/otimizador/'

    Com Spark ativo (Databricks) e destino remoto, escreve via Spark. Sem Spark,
    escreve com pandas (parquet ou csv) — bom para testar no Colab.

    IDEMPOTENTE POR RODADA: gravar duas vezes o mesmo `run_id` SUBSTITUI a particao
    daquela rodada, nao acrescenta. E o que faz o retry do job ser seguro — e sem
    isso o `blob_uri` da auditoria passaria a apontar para linhas em dobro. Rodadas
    diferentes nunca se tocam, porque cada uma e uma particao.

    A garantia depende de `particionar_por_run=True` (o padrao). Com ela desligada
    nao existe particao a substituir, e cada gravacao so pode acrescentar.
    """
    sp = _spark()
    remoto = str(destino).startswith(("abfss://", "wasbs://", "s3://", "gs://", "dbfs:/"))
    rid = tabs["run_meta"]["run_id"].iloc[0] if "run_meta" in tabs else novo_run_id()
    escritos = []
    for nome, df in tabs.items():
        if df is None or len(df) == 0:
            continue
        base = str(destino).rstrip("/") + "/" + nome
        if sp is not None and (remoto or formato == "delta"):
            sdf = sp.createDataFrame(df)
            fmt = "delta" if formato == "delta" else formato
            particiona = particionar_por_run and "run_id" in df.columns
            w = sdf.write.format(fmt)
            if not particiona:
                w.mode("append").save(base)
            elif fmt == "delta":
                rids = _rodadas_no_df(df, rid)
                if _delta_existe(sp, base):
                    # `replaceWhere` e o DELETE+INSERT do Delta: troca so as linhas
                    # desta rodada e deixa as outras intactas. Se a condicao nao for
                    # aceita, ele FALHA — que e o que se quer de um caminho de
                    # escrita, em vez de sobrescrever a tabela inteira em silencio.
                    cond = " OR ".join(f"run_id = '{r}'" for r in rids)
                    w = w.mode("overwrite").option("replaceWhere", cond)
                else:
                    w = w.mode("append")  # primeira gravacao: nao ha o que substituir
                w.partitionBy("run_id").save(base)
            else:
                _apagar_particoes_spark(sp, base, _rodadas_no_df(df, rid))
                w.mode("append").partitionBy("run_id").save(base)
            escritos.append((nome, base, len(df)))
        else:
            rid_tab = _rodadas_no_df(df, rid)[0]
            pasta = base if not particionar_por_run else f"{base}/run_id={rid_tab}"
            if particionar_por_run and _os.path.isdir(pasta):
                # Mesma regra do lado Spark: a particao da rodada e SUBSTITUIDA.
                # Nao basta reescrever `dados.parquet` por cima: se a execucao
                # anterior tiver caido no fallback e deixado um `dados.csv`, os dois
                # conviveriam na pasta — e `carregar()` le TUDO que estiver nela.
                _shutil.rmtree(pasta)
            _os.makedirs(pasta, exist_ok=True)
            if formato == "csv":
                cam = f"{pasta}/dados.csv"
                df.to_csv(cam, index=False, encoding="utf-8-sig")
            else:
                cam = f"{pasta}/dados.parquet"
                try:
                    df.to_parquet(cam, index=False)
                except Exception:
                    cam = f"{pasta}/dados.csv"
                    df.to_csv(cam, index=False, encoding="utf-8-sig")
            escritos.append((nome, cam, len(df)))
    if verbose:
        print(f"{len(escritos)} tabela(s) gravada(s) em {destino}  (run_id={rid})")
        for nome, cam, n in escritos[:6]:
            print(f"  {nome:<28}{n:>9,} linhas   {cam}")
        if len(escritos) > 6:
            print(f"  ... e mais {len(escritos)-6} tabela(s)")
    return escritos


def salvar_delta(tabs, schema, modo=None, particionar_por_run=True, verbose=True):
    """Databricks: grava como TABELAS Delta gerenciadas em `schema`
    (ex.: 'principal.otimizador'). Os arquivos ficam no storage do catalogo.

    `modo=None` (padrao) SUBSTITUI a particao da rodada, via `replaceWhere`. Para
    rodadas novas isso equivale a acrescentar — nenhuma linha casa com a condicao —
    e para uma rodada ja gravada troca as linhas dela sem tocar nas outras. Era
    `modo="append"`, que fazia reexecutar o mesmo `run_id` DUPLICAR as linhas.

    `modo` explicito volta ao modo cru do Spark, para quem quiser mesmo append.
    """
    sp = _spark()
    if sp is None:
        raise RuntimeError("Spark nao encontrado — use salvar() para gravar em arquivo.")
    feitas = []
    for nome, df in tabs.items():
        if df is None or len(df) == 0:
            continue
        alvo = f"{schema}.{nome}"
        sdf = sp.createDataFrame(df)
        particiona = particionar_por_run and "run_id" in df.columns
        w = sdf.write.format("delta").option("mergeSchema", "true")
        if modo is not None:
            w = w.mode(modo)
        elif particiona and _tabela_existe(sp, alvo):
            cond = " OR ".join(f"run_id = '{r}'" for r in _rodadas_no_df(df, ""))
            w = w.mode("overwrite").option("replaceWhere", cond)
        else:
            w = w.mode("append")  # tabela ainda nao existe: nao ha o que substituir
        if particiona:
            w = w.partitionBy("run_id")
        w.saveAsTable(alvo)
        feitas.append((alvo, len(df)))
    if verbose:
        print(f"{len(feitas)} tabela(s) Delta gravada(s) em {schema}")
        for alvo, n in feitas:
            print(f"  {alvo:<44}{n:>9,} linhas")
    return feitas


def carregar(destino, run_id=None, tabelas=None, formato="parquet"):
    """Le de volta o que salvar() gravou (modo arquivo/pandas). Devolve {tabela: DataFrame}."""
    out = {}
    base = str(destino).rstrip("/")
    if not _os.path.isdir(base):
        raise FileNotFoundError(base)
    for nome in sorted(_os.listdir(base)):
        if tabelas and nome not in tabelas:
            continue
        pasta = _os.path.join(base, nome)
        if not _os.path.isdir(pasta):
            continue
        partes = []
        for sub in sorted(_os.listdir(pasta)):
            if run_id and sub != f"run_id={run_id}":
                continue
            cam_dir = _os.path.join(pasta, sub)
            if _os.path.isdir(cam_dir):
                for arq in sorted(_os.listdir(cam_dir)):
                    cam = _os.path.join(cam_dir, arq)
                    partes.append(pd.read_parquet(cam) if arq.endswith(".parquet")
                                  else pd.read_csv(cam))
            elif sub.endswith((".parquet", ".csv")):
                partes.append(pd.read_parquet(cam_dir) if sub.endswith(".parquet")
                              else pd.read_csv(cam_dir))
        if partes:
            out[nome] = pd.concat(partes, ignore_index=True)
    return out


def comparar_runs(destino, run_ids=None):
    """Tabela comparativa de cenarios a partir do run_meta gravado."""
    metas = carregar(destino, tabelas=["run_meta"]).get("run_meta")
    if metas is None or metas.empty:
        print("nenhum run_meta encontrado em " + str(destino))
        return None
    if run_ids:
        metas = metas[metas.run_id.isin(run_ids)]
    cols = ["run_id", "data_hora", "anos_capex", "foco_cobertura", "penalidade_cobertura",
            "orcamento_total", "vpl", "capex_total", "obras_construidas", "subbacias_faturando",
            "metas_total", "metas_nao_atingidas", "vp_efeito_base", "auditoria_ok", "milp_status"]
    return metas[[c for c in cols if c in metas.columns]].sort_values("data_hora")

def exportar_excel(tabs, caminho="resultado_otimizacao.xlsx", incluir_snapshot=False,
                   limite_linhas=200000):
    """Uma pasta de trabalho com uma aba por tabela — para conferir no Excel."""
    alvo = {k: v for k, v in tabs.items()
            if (incluir_snapshot or not k.startswith("snapshot__")) and v is not None and len(v)}
    with pd.ExcelWriter(caminho, engine="openpyxl") as w:
        for nome, df in alvo.items():
            aba = nome.replace("run_", "")[:31]
            df.head(limite_linhas).to_excel(w, sheet_name=aba, index=False)
    print(f"{len(alvo)} aba(s) em {caminho}")
    return caminho


def exportar_zip(pasta, caminho="resultado_otimizacao.zip"):
    """Compacta a pasta de resultados — util para baixar do Colab de uma vez."""
    import shutil
    base = caminho[:-4] if caminho.endswith(".zip") else caminho
    z = shutil.make_archive(base, "zip", pasta)
    print(f"{z}  ({_os.path.getsize(z)/1e6:.1f} MB)")
    return z
