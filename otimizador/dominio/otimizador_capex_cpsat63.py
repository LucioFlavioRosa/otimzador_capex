# -*- coding: utf-8 -*-
"""
BACKEND CP-SAT (OR-Tools) — v27: OBRIGATORIAS PRIMEIRO (Fase 0 lexicografica).
Diferenca vs v26: resolver_por_sistema aloca o MAXIMO de obras obrigatorias que couber
no teto anual ANTES de qualquer obra opcional; so depois otimiza metas/cobertura/VPL.

Por que CP-SAT: o problema e de SELECAO + AGENDAMENTO com restricoes LOGICAS
(elegivel => construido, gating em cascata, Yc = MAX das conclusoes). O CP-SAT
modela isso nativamente (implicacoes, OnlyEnforceIf, AddMaxEquality) SEM big-M,
o que costuma ser ordens de magnitude mais rapido que o MILP aqui.

O VPL (reais) e escalado para INTEIROS (o CP-SAT exige objetivo inteiro).
Interface identica a do MILP: resolver_cpsat(cen, ...) e resolver_cpsat_por_regional(cen, ...).
Rode no Colab:  pip install ortools
"""
import math
from otimizador.dominio import otimizador_capex_v62 as M   # unica linha alterada na reorganizacao
def _orck(cen):   # v26: sem financiamento -> sempre o CAPEX cheio entra na restricao de orcamento
    return "capex_ano"
import copy as _copy

def _coefs(cen):
    def capex_pv(o,y):
        tx=cen.taxa_de(o);Hm=cen.horizonte(o)*12;pe=o.prazo;v=0.0
        if pe>0:
            cm=o.capex/pe
            for m in range(y,min(y+pe,Hm)): v+=cm/(1+tx)**(m//12)
        elif y<Hm: v+=o.capex/(1+tx)**(y//12)
        return v
    def modulo_pv(e,y):
        tx=cen.taxa_de(e);Hm=cen.horizonte(e)*12;pe=e.prazo;cap=e.capex_modulo;v=0.0
        if pe>0:
            cm=cap/pe
            for m in range(y,min(y+pe,Hm)): v+=cm/(1+tx)**(m//12)
        elif y<Hm: v+=cap/(1+tx)**(y//12)
        return v
    def pv_lump(o,total,y):
        tx=cen.taxa_de(o);Hm=cen.horizonte(o)*12;pe=o.prazo;v=0.0
        if pe>0:
            cm=total/pe
            for m in range(y,min(y+pe,Hm)): v+=cm/(1+tx)**(m//12)
        elif y<Hm: v+=total/(1+tx)**(y//12)
        return v
    def receita_pv(c,k): return M._pv_receita(cen,c,(k+1)*12+c.lag)
    def opex_ano_desc(o,Y): return o.opex_ano/(1+cen.taxa_de(o))**Y if Y<cen.horizonte(o) else 0.0
    def opexsum(o,F):
        tx=cen.taxa_de(o);H=cen.horizonte(o);v=0.0
        for Y in range(F,H): v+=o.opex_ano/(1+tx)**Y
        return v
    def compY(o,t): return (t+o.prazo)//12
    def fatY(c,k):  return ((k+1)*12+c.lag)//12
    return capex_pv,modulo_pv,pv_lump,receita_pv,opex_ano_desc,opexsum,compY,fatY

def _perm(cen,oid,grid):
    o=cen.obras[oid]; ms=M.meses_permitidos(cen,o)
    if grid<=1: return ms
    f=[t for t in ms if t%grid==0]
    if o.inicio_min in ms and o.inicio_min not in f: f=[o.inicio_min]+f
    return f or ms

def resolver_cpsat(cen, max_time_s=120, workers=8, grid_meses=12, meta_hard=False, SC=1, VZ=10, msg=False):
    from ortools.sat.python import cp_model
    md=cp_model.CpModel()
    R=lambda v:int(round(v*SC))
    anos=cen.anos; maxpe=max((o.prazo for o in cen.obras.values()),default=0)
    Kmax=min(anos-1, int(getattr(cen,"anos_capex",anos))+maxpe//12+2)
    capex_pv,modulo_pv,pv_lump,receita_pv,opex_ano_desc,opexsum,compY,fatY=_coefs(cen)
    EF=getattr(cen,'ete_fixo',False)

    aeg=[oid for oid,o in cen.obras.items() if o.tipo!="ete" and o.responsavel=="Aegea"]
    etes=list(cen.ete_do_sistema.values()); ete_ids=[e.id for e in etes]; build=aeg+ete_ids
    perm={oid:_perm(cen,oid,grid_meses) for oid in build}
    sis_sb={}
    for sb in cen.nos: sis_sb.setdefault(cen.nos[sb].sistema,[]).append(sb)

    x={oid:{t:md.NewBoolVar(f"x_{oid}_{t}") for t in perm[oid]} for oid in build}
    built={}
    for oid in build:
        b=md.NewBoolVar(f"b_{oid}"); md.Add(sum(x[oid].values())==b); built[oid]=b
        if cen.obras[oid].obrigatoria: md.Add(b==1)
    def compYexpr(oid): 
        o=cen.obras[oid]; return sum(compY(o,t)*x[oid][t] for t in perm[oid])

    # ---- ETE: modulos (expansao) ----
    nmod={};zmod={}
    for e in etes:
        if EF or getattr(e,"nova",False): continue
        cap=e.cap_modulo or 1e-9
        Nmax=int(math.ceil(sum(cen.vazao.get(sb,0.0) for sb in sis_sb.get(e.sistema,[]))/cap))+1
        nmod[e.id]=md.NewIntVar(0,Nmax,f"nmod_{e.id}")
        md.Add(nmod[e.id]<=Nmax*built[e.id]); md.Add(nmod[e.id]>=built[e.id])
        zmod[e.id]={}
        for t in perm[e.id]:
            z=md.NewIntVar(0,Nmax,f"z_{e.id}_{t}")
            md.Add(z==nmod[e.id]).OnlyEnforceIf(x[e.id][t]); md.Add(z==0).OnlyEnforceIf(x[e.id][t].Not())
            zmod[e.id][t]=z

    # ---- conectividade (AND dos componentes do caminho) — so no modo modular ----
    conect={}
    if not EF:
     for sb in cen.nos:
        rr=[r for r in M._reqs_sem_ete(cen,sb) if r.eh_aegea()]
        c=md.NewBoolVar(f"con_{sb}"); conect[sb]=c
        if not any(o.tipo=="coleta" for o in M._reqs_sem_ete(cen,sb)) or not rr:
            md.Add(c==0)
        else:
            md.AddMinEquality(c,[built[r.id] for r in rr])   # AND

    # ---- capacidade da ETE (so no modo modular) ----
    for e in ([] if EF else etes):
        dem=sum(int(round(cen.vazao.get(sb,0.0)*VZ))*conect[sb] for sb in sis_sb.get(e.sistema,[]))
        if getattr(e,"nova",False):
            md.Add(dem<=int(round(e.modulos*e.cap_modulo*VZ)))
            for sb in sis_sb.get(e.sistema,[]): md.Add(built[e.id]>=conect[sb])
        else:
            md.Add(nmod[e.id]*int(round(e.cap_modulo*VZ)) >= dem - int(round(e.folga*VZ)))

    # ---- receita (u[c,k]) ----
    coletas=[c for c in cen.coletas if c.necessaria]; u={}
    for c in coletas:
        u[c.id]={k:md.NewBoolVar(f"u_{c.id}_{k}") for k in range(Kmax+1)}
        elig=md.NewBoolVar(f"e_{c.id}"); md.Add(sum(u[c.id].values())==elig)
        reqs_ne=M._reqs_sem_ete(cen,c.no)
        for r in reqs_ne:
            if r.eh_aegea(): md.AddImplication(elig,built[r.id])
        Yc=sum(k*u[c.id][k] for k in range(Kmax+1))
        for r in reqs_ne:
            if r.eh_aegea(): md.Add(Yc>=compYexpr(r.id)).OnlyEnforceIf(elig)
            elif r.responsavel=="Terceiro": md.Add(Yc>=(r.prazo//12)).OnlyEnforceIf(elig)
        e=cen.ete_do_sistema.get(cen.nos[c.no].sistema)
        if e is not None:
            md.Add(Yc>=compYexpr(e.id)).OnlyEnforceIf(elig)
            if EF or getattr(e,"nova",False): md.AddImplication(elig,built[e.id])

    # ---- OPEX das ETEs (indicador anual) ----
    op={}
    def faturou(cid,Y): return sum(u[cid][k] for k in range(Kmax+1) if fatY(cen.obras[cid],k)<=Y)
    for e in etes:
        if e.opex_ano<=0: continue
        op[e.id]={Y:md.NewBoolVar(f"op_{e.id}_{Y}") for Y in range(cen.horizonte(e))}
        for Y in op[e.id]:
            md.Add(op[e.id][Y]<=built[e.id])
            for sb in sis_sb.get(e.sistema,[]):
                cid="lig_"+sb
                if cid in u: md.Add(op[e.id][Y]>=faturou(cid,Y)).OnlyEnforceIf(built[e.id])

    # ---- meta de cobertura (opcional) ----
    if meta_hard:
        for cidnome,cd in cen.cidades.items():
            terms=[int(round(c.lig))*u[c.id][k] for c in coletas if cen.cidade_da(c)==cidnome for k in range(Kmax+1)]
            if terms and cd.meta_aumento>0: md.Add(sum(terms)>=int(round(cd.meta_aumento)))

    # ---- orcamento por regional/ano ----
    for reg in cen.regionais:
        for Y in range(anos):
            vs=[];cs=[]
            for oid in aeg:
                o=cen.obras[oid]
                if cen.regional_da(o)!=reg: continue
                pe=o.prazo
                for t in perm[oid]:
                    val=0.0
                    if pe>0:
                        cm=o.capex/pe
                        for m in range(t,t+pe):
                            if m//12==Y: val+=cm
                    elif t//12==Y: val=o.capex
                    if val>0: vs.append(x[oid][t]); cs.append(R(val))
            for e in etes:
                if cen.regional_da(e)!=reg: continue
                pe=e.prazo
                if EF:
                    tot=e.capex_fixo; cm=tot/pe if pe>0 else 0
                    for t in perm[e.id]:
                        val=0.0
                        if pe>0:
                            for m in range(t,t+pe):
                                if m//12==Y: val+=cm
                        elif t//12==Y: val=tot
                        if val>0: vs.append(x[e.id][t]); cs.append(R(val))
                elif getattr(e,"nova",False):
                    tot=e.capex_terreno+e.modulos*e.capex_modulo; cm=tot/pe if pe>0 else 0
                    for t in perm[e.id]:
                        val=0.0
                        if pe>0:
                            for m in range(t,t+pe):
                                if m//12==Y: val+=cm
                        elif t//12==Y: val=tot
                        if val>0: vs.append(x[e.id][t]); cs.append(R(val))
                else:
                    cm=e.capex_modulo/pe if pe>0 else 0
                    for t in perm[e.id]:
                        val=0.0
                        if pe>0:
                            for m in range(t,t+pe):
                                if m//12==Y: val+=cm
                        elif t//12==Y: val=e.capex_modulo
                        if val>0: vs.append(zmod[e.id][t]); cs.append(R(val))
            if vs: md.Add(cp_model.LinearExpr.WeightedSum(vs,cs) <= R(cen.orc[reg][Y]))

    # ---- objetivo (inteiro) ----
    OV=[];OC=[]
    def addterm(var,coef):
        if coef!=0: OV.append(var); OC.append(coef)
    for oid in aeg:
        o=cen.obras[oid]
        for t in perm[oid]: addterm(x[oid][t],-R(capex_pv(o,t)))
    for e in etes:
        if EF:
            for t in perm[e.id]: addterm(x[e.id][t],-R(pv_lump(e,e.capex_fixo,t)))
        elif getattr(e,"nova",False):
            tot=e.capex_terreno+e.modulos*e.capex_modulo
            for t in perm[e.id]: addterm(x[e.id][t],-R(pv_lump(e,tot,t)))
        else:
            for t in perm[e.id]: addterm(zmod[e.id][t],-R(modulo_pv(e,t)))
    for c in coletas:
        for k in range(Kmax+1): addterm(u[c.id][k],R(receita_pv(c,k)))
    for oid,o in cen.obras.items():
        if o.opex_ano<=0 or o.tipo=="ete": continue
        cid="lig_"+o.no
        if cid in u:
            cc=cen.obras[cid]
            for k in range(Kmax+1): addterm(u[cid][k],-R(opexsum(o,((k+1)*12+cc.lag)//12)))
    for eid,d in op.items():
        e=cen.obras[eid]
        for Y,v in d.items(): addterm(v,-R(opex_ano_desc(e,Y)))
    md.Maximize(cp_model.LinearExpr.WeightedSum(OV,OC))

    solver=cp_model.CpSolver()
    solver.parameters.max_time_in_seconds=float(max_time_s)
    solver.parameters.num_search_workers=int(workers)
    if msg: solver.parameters.log_search_progress=True
    st=solver.Solve(md)
    plano={oid:None for oid in cen.obras}
    ok=st in (cp_model.OPTIMAL,cp_model.FEASIBLE)
    if ok:
        for oid in build:
            for t in perm[oid]:
                if solver.Value(x[oid][t])==1: plano[oid]=t
    res=M.avaliar(cen,plano)
    res["milp_status"]=("OTIMO" if st==cp_model.OPTIMAL else "VIAVEL(limite de tempo)" if st==cp_model.FEASIBLE else f"SEM SOLUCAO({st})")
    res["milp_bound"]=solver.BestObjectiveBound()/max(SC,1)
    res["milp_solver"]="CP-SAT"
    return res

if __name__=="__main__":
    cen=M.ler_banco("banco_dados_v16.xlsx", orcamento=2_000_000, horizonte_capex=5)
    res=resolver_cpsat(cen, max_time_s=30)
    M.imprimir(cen,res,f"PLANO CP-SAT ({res['milp_status']})")
    print("bound:",res["milp_bound"])

# ==================== DECOMPOSICAO POR SISTEMA (dentro de uma regional) ====================
def _sub_cenario_sistema(cen, sis):
    nos=[n for n in cen.nos.values() if n.sistema==sis]; no_ids={n.id for n in nos}
    obras=[o for o in cen.obras.values() if (o.tipo in ("ete","ete_mod") and o.sistema==sis) or (o.tipo not in ("ete","ete_mod") and o.no in no_ids)]
    reg=nos[0].regional; cidn={n.cidade for n in nos}
    cids=[c for cid,c in cen.cidades.items() if cid in cidn]
    s=M.Cenario(nos,cids,obras,{reg:[1e15]*cen.anos},hz={sis:cen.hz[sis]})
    s.vazao={sb:cen.vazao.get(sb,0.0) for sb in no_ids}
    s.anos_capex=int(getattr(cen,"anos_capex",cen.anos))
    FAS=getattr(cen,"ete_faseada",False)
    s.ete_faseada=FAS; s.ete_fixo=False
    s.max_lig={sis:getattr(cen,"max_lig",{}).get(sis,0.0)}
    s.base_lig={sis:getattr(cen,"base_lig",{}).get(sis,0.0)}
    s.metas_cobertura={sis:getattr(cen,"metas_cobertura",{}).get(sis,{})}
    s.peso_cobertura=getattr(cen,"peso_cobertura",0.0)
    s.ano_base={sis:getattr(cen,"ano_base",{}).get(sis,0)}
    s.penalidade_cobertura=getattr(cen,"penalidade_cobertura","meta+cobertura")
    s.total_max_lig=getattr(cen,"total_max_lig",0.0)
    s.fator_esgoto={_c:list((getattr(cen,"fator_esgoto",{}) or {}).get(_c,[])) for _c in cidn}
    s.sub_receita={_sb:(getattr(cen,"sub_receita",{}) or {}).get(_sb,{}) for _sb in no_ids}
    s.unid_fator={_sb:(getattr(cen,"unid_fator",{}) or {}).get(_sb,1.0) for _sb in no_ids}
    s.unidade_cobertura=dict(getattr(cen,"unidade_cobertura",{}) or {})
    if FAS:                                                    # modulos como obras (trava de capacidade em avaliar)
        s.modulos_sis={sis:[o for o in obras if o.tipo=="ete_mod"]}
        return s
    s.ete_fixo=True                                           # (nao-faseado) SOLVE usa ETE fixa (proxy rapido)
    sbmap={}
    for n in s.nos.values(): sbmap.setdefault(n.sistema,[]).append(n.id)
    for e in s.ete_do_sistema.values():
        tot=sum(s.vazao.get(sb,0.0) for sb in sbmap.get(e.sistema,[]))
        if getattr(e,"nova",False): e.capex_fixo=e.capex_terreno+e.modulos*e.capex_modulo
        else:
            exc=max(0.0,tot-e.folga)
            e.capex_fixo=(int(math.ceil(exc/e.cap_modulo)) if (exc>1e-9 and e.cap_modulo>0) else (1 if exc>1e-9 else 0))*e.capex_modulo
    return s

def _montar_faseado(sub, built, shift_meses=0):
    """Plano do sistema servindo o subconjunto 'built' de sub-bacias, tudo o mais cedo possivel (+shift).
    Os modulos-obra necessarios entram tambem o mais cedo possivel."""
    pl={oid:None for oid in sub.obras}
    for oid,o in sub.obras.items():
        if o.no in built and o.eh_aegea() and o.tipo!="ete_mod":
            _py=getattr(o,"_obrig_planyear",None)
            if _py is not None: pl[oid]=min(max(int(o.inicio_min),(_py-1)*12), _py*12-1)   # obrigatoria em ano EXATO: fica na janela
            else: pl[oid]=o.inicio_min+shift_meses
    for sis,mm in getattr(sub,"modulos_sis",{}).items():
        e=sub.ete_do_sistema.get(sis)
        tot=sum(sub.vazao.get(sb,0.0) for sb in built if sub.nos[sb].sistema==sis)
        need=int(math.ceil(max(0.0,tot-e.folga)/e.cap_modulo)) if (e and e.cap_modulo>0) else (1 if (e and tot>e.folga) else 0)
        for k,mo in enumerate(mm):
            pl[mo.id]=(mo.inicio_min+shift_meses) if k<need else None
    return pl

def _plano_sistema_faseado(sub):
    """Otimo do sistema SEM teto (uncapped): constroi todas as sub-bacias que se pagam, o quanto antes,
    e poda as de VPL marginal negativo (removendo tambem os modulos que ficam sem uso)."""
    sbs=[n.id for n in sub.nos.values() if any(o.no==n.id and o.eh_aegea() and o.tipo!="ete_mod" for o in sub.obras.values())]
    locked={n.id for n in sub.nos.values() if any(o.no==n.id and o.obrigatoria and o.eh_aegea() for o in sub.obras.values())}
    built=set(sbs); best=M.avaliar(sub,_montar_faseado(sub,built))["vpl_obj"]
    changed=True
    while changed and (built-locked):
        changed=False; rm=None; bv=best
        for sb in list(built-locked):
            v=M.avaliar(sub,_montar_faseado(sub,built-{sb}))["vpl_obj"]
            if v>bv+1e-6: bv=v; rm=sb
        if rm is not None: built.discard(rm); best=bv; changed=True
    return built

def _colunas_faseada(cen,sis,sub,reg,anos,ac):
    built=_plano_sistema_faseado(sub)
    _r0=M.avaliar(sub,{oid:None for oid in sub.obras})           # "nada": nao constroi nada (deficit maximo)
    _cov=lambda rr: sum((v[-1] if v else 0.0) for v in rr.get("cobertura_sistema",{}).values())
    _locked={n.id for n in sub.nos.values() if any(o.no==n.id and o.obrigatoria and o.eh_aegea() for o in sub.obras.values())}
    cols=[(_r0["vpl_obj"],list(_r0[_orck(cen)][reg]),{oid:None for oid in sub.obras},_r0["vpl"],_r0["metas_nao_atingidas"],_cov(_r0))]
    seen=set()
    vfull=M.avaliar(sub,_montar_faseado(sub,built))["vpl_obj"] if built else _r0["vpl_obj"]
    marg=sorted((vfull-M.avaliar(sub,_montar_faseado(sub,built-{sb}))["vpl_obj"], sb) for sb in (built-_locked))
    subsets=[set(built)]; cur=set(built)
    for _,sb in marg:                                            # subconjuntos decrescentes (flexibilidade de orcamento)
        cur=cur-{sb}
        if cur: subsets.append(set(cur))
    for bs in subsets:
        for d in range(ac):
            pl=_montar_faseado(sub,bs,d*12)
            if any(v is not None and v>=ac*12 for v in pl.values()): break
            if not M.viavel(sub,pl)[0]: continue
            r=M.avaliar(sub,pl); key=round(r["vpl_obj"])
            if key in seen: continue
            seen.add(key); cols.append((r["vpl_obj"],list(r[_orck(cen)][reg]),dict(pl),r["vpl"],r["metas_nao_atingidas"],_cov(r)))
    return cols

def _sub_cenario_cidade(cen, cid):
    """Subcenario de uma CIDADE: TODOS os sistemas (sub-bacias + ETEs) dela.
    A meta de cobertura e por cidade, entao os sistemas da mesma cidade sao resolvidos JUNTOS."""
    nos=[n for n in cen.nos.values() if n.cidade==cid]; no_ids={n.id for n in nos}
    sisset={n.sistema for n in nos}
    obras=[o for o in cen.obras.values() if (o.tipo in ("ete","ete_mod") and o.sistema in sisset) or (o.tipo not in ("ete","ete_mod") and o.no in no_ids)]
    reg=nos[0].regional; cids=[c for cn,c in cen.cidades.items() if cn==cid]
    hz={sn:cen.hz[sn] for sn in sisset if sn in cen.hz}
    s=M.Cenario(nos,cids,obras,{reg:[1e15]*cen.anos},hz=hz)
    s.vazao={sb:cen.vazao.get(sb,0.0) for sb in no_ids}
    s.anos_capex=int(getattr(cen,"anos_capex",cen.anos))
    FAS=getattr(cen,"ete_faseada",False); s.ete_faseada=FAS; s.ete_fixo=False
    s.max_lig={cid:getattr(cen,"max_lig",{}).get(cid,0.0)}
    s.base_lig={cid:getattr(cen,"base_lig",{}).get(cid,0.0)}
    s.metas_cobertura={cid:getattr(cen,"metas_cobertura",{}).get(cid,{})}
    s.peso_cobertura=getattr(cen,"peso_cobertura",0.0)
    s.ano_base={cid:getattr(cen,"ano_base",{}).get(cid,0)}
    s.penalidade_cobertura=getattr(cen,"penalidade_cobertura","meta+cobertura")
    s.total_max_lig=getattr(cen,"total_max_lig",0.0)
    s.fator_esgoto={cid:list((getattr(cen,"fator_esgoto",{}) or {}).get(cid,[]))}        # faixas esgoto/agua da cidade
    s.sub_receita={_sb:(getattr(cen,"sub_receita",{}) or {}).get(_sb,{}) for _sb in no_ids}  # base existente (efeito-base)
    s.unid_fator={_sb:(getattr(cen,"unid_fator",{}) or {}).get(_sb,1.0) for _sb in no_ids}    # unidade de cobertura
    s.unidade_cobertura=dict(getattr(cen,"unidade_cobertura",{}) or {})
    if FAS:
        s.modulos_sis={sn:[oo for oo in obras if oo.tipo=="ete_mod" and oo.sistema==sn] for sn in sisset}
        return s
    s.ete_fixo=True; sbmap={}
    for n in s.nos.values(): sbmap.setdefault(n.sistema,[]).append(n.id)
    for e in s.ete_do_sistema.values():
        tot=sum(s.vazao.get(sb,0.0) for sb in sbmap.get(e.sistema,[]))
        if getattr(e,"nova",False): e.capex_fixo=e.capex_terreno+e.modulos*e.capex_modulo
        else:
            exc=max(0.0,tot-e.folga)
            e.capex_fixo=(int(math.ceil(exc/e.cap_modulo)) if (exc>1e-9 and e.cap_modulo>0) else (1 if exc>1e-9 else 0))*e.capex_modulo
    return s

def _colunas_sistema(cen, sis, col_time_s=5, col_grid=12):
    """Gera colunas (planos) para um sistema: o plano otimo (CP-SAT) + versoes deslocadas no tempo + 'nada'.
    Cada coluna = (vpl, perfil_capex_por_ano, plano_local)."""
    reg=list(cen.regionais)[0]; anos=cen.anos; ac=int(getattr(cen,"anos_capex",anos))
    sub=_sub_cenario_cidade(cen,sis)
    if getattr(cen,"ete_faseada",False):
        return _colunas_faseada(cen,sis,sub,reg,anos,ac)
    p0=resolver_cpsat(sub, max_time_s=col_time_s, workers=1, grid_meses=col_grid)["plano"]   # timings (proxy fixo, rapido)
    ev=sub                                                    # cenario de AVALIACAO das colunas
    if getattr(cen,"ete_faseada",False):
        ev=_copy.copy(sub); ev.ete_fixo=False; ev.ete_faseada=True   # valor com ETE FASEADA
    _cov=lambda rr: sum((v[-1] if v else 0.0) for v in rr.get("cobertura_sistema",{}).values())
    _rn0=M.avaliar(ev,{oid:None for oid in sub.obras}); cols=[(0.0,[0.0]*anos,{oid:None for oid in sub.obras},_rn0["vpl"],_rn0["metas_nao_atingidas"],_cov(_rn0))]  # "nada"
    seen=set()
    for d in range(ac):
        pl={oid:(v+d*12 if v is not None else None) for oid,v in p0.items()}
        if any(v is not None and v>=ac*12 for v in pl.values()): break   # saiu da janela de CAPEX
        if not M.viavel(ev,pl)[0]: continue
        r=M.avaliar(ev,pl); key=round(r["vpl"])
        if key in seen: continue
        seen.add(key); cols.append((r["vpl"], list(r[_orck(cen)][reg]), dict(pl), r["vpl"], r["metas_nao_atingidas"], _cov(r)))
    return cols

def _desconsidera_obrig_fora_janela(cen, verbose=True):
    """Uma obra OBRIGATORIA que NAO cabe inteira na janela de CAPEX (inicio + execucao passa de
    anos_capex) e DESCONSIDERADA como obrigatoria -> vira opcional, do mesmo modo que metas de
    cobertura fora do horizonte de CAPEX sao ignoradas. Assim ela nao forca estouro de teto num
    ano sem verba. Retorna a lista de obras rebaixadas (armazenada em cen._obrig_desconsideradas)."""
    Hm=int(getattr(cen,"anos_capex",cen.anos))*12
    fora=[]
    for oid,o in cen.obras.items():
        if not (getattr(o,"obrigatoria",False) and o.eh_aegea()): continue
        starts=M.meses_permitidos(cen,o)                 # ja restrito ao ano-exato e a < anos_capex*12
        cabe=bool(starts)   # meses_permitidos ja restringe inicio a janela e conclusao a janela+anos_extra
        if not cabe:
            fora.append(oid)
            o.obrigatoria=False                          # deixa de ser forcada (passa a opcional)
            try: o._obrig_planyear=None                  # sem pino de ano: inicia em qualquer mes da janela
            except Exception: pass
    cen._obrig_desconsideradas=list(fora)
    if fora and verbose:
        print(f"  [info] {len(fora)} obrigatoria(s) FORA da janela de CAPEX -> desconsideradas (como metas fora do horizonte): "
              f"{fora[:8]}"+(" ..." if len(fora)>8 else ""))
    return fora

def resolver_por_sistema(cen, max_time_s=60, workers=8, verbose=True, col_time_s=5, col_grid=12,
                         gap_relativo=0.0):
    """Decomposicao por CIDADE.
    FASE 0 (prioridade ABSOLUTA): construir o MAXIMO de obras OBRIGATORIAS que couber no teto
    anual, ANTES de qualquer obra opcional. Esse piso e travado nas fases seguintes.
    Depois: foco>=0.95 -> LEXICOGRAFICO (metas 1o; cobertura 2o; RETORNO como desempate);
    foco<0.95 -> ponderado.
    Se alguma obrigatoria nao couber no orcamento, avisa exatamente quais (sem estourar o teto).

    gap_relativo: para a busca quando a solucao esta comprovadamente a menos de `gap_relativo`
    do otimo (0.02 = 2%). 0.0 (default) mantem o comportamento historico: so para por prova
    exata ou por relogio. Sem ele uma fase pode gastar o teto inteiro PROVANDO um plano que ja
    tinha achado — medido numa unidade de 67 cidades: 339s de 720s, com o resultado final a
    0,006% do limite superior provado."""
    from ortools.sat.python import cp_model
    import time as _t
    reg=list(cen.regionais)[0]; anos=cen.anos
    _desconsidera_obrig_fora_janela(cen, verbose)         # obrigatoria fora da janela de CAPEX -> vira opcional
    grupos=sorted({n.cidade for n in cen.nos.values()})
    t0=_t.time(); cols={g:_colunas_sistema(cen,g,col_time_s=col_time_s,col_grid=col_grid) for g in grupos}
    _pc=getattr(cen,"peso_cidade",{}) or {}                      # PESO POR CIDADE (default 1.0)
    for _g in cols:
        _w=float(_pc.get(_g,1.0))
        cols[_g]=[(vpl,(list(prof)+[0.0]*anos)[:anos],pl,vr,mn*_w,cv*_w) for (vpl,prof,pl,vr,mn,cv) in cols[_g]]
    if verbose: print(f"colunas: {sum(len(v) for v in cols.values())} ({len(grupos)} cidades) em {_t.time()-t0:.0f}s")

    # ---- obras OBRIGATORIAS por CIDADE (inclui ETEs, via cidade_da) ----
    obrig_por_cidade={}
    for oid,o in cen.obras.items():
        if o.obrigatoria and o.eh_aegea():
            g=cen.cidade_da(o); obrig_por_cidade[g]=obrig_por_cidade.get(g,0)+1
    obrig_cid=set(obrig_por_cidade); tot_obrig=sum(obrig_por_cidade.values())
    # 'nada' (todas None) e a UNICA coluna por cidade que NAO constroi as obrigatorias
    nada_idx={g:next((j for j,col in enumerate(cols[g]) if all(v is None for v in col[2].values())),None) for g in grupos}

    def _base():
        md=cp_model.CpModel(); y={}
        for gi,g in enumerate(grupos):
            yy=[md.NewBoolVar(f"y_{gi}_{j}") for j in range(len(cols[g]))]
            md.AddExactlyOne(yy); y[g]=yy
        _ac=int(getattr(cen,"anos_capex",anos))
        for Y in range(_ac):                             # teto ANUAL: SO na janela de CAPEX
            vs=[];cs=[]
            for g in grupos:
                for j,col in enumerate(cols[g]):
                    pv=col[1][Y] if Y<len(col[1]) else 0.0
                    if pv>1: vs.append(y[g][j]); cs.append(int(round(pv)))
            if vs: md.Add(cp_model.LinearExpr.WeightedSum(vs,cs) <= int(round(cen.orc[reg][Y])))
        _teto_j=cen.orc_total if getattr(cen,"orc_total",None) else sum(cen.orc[reg][:_ac])
        tv=[];tc=[]                                      # teto TOTAL da janela (custeia o rabo pos-janela via sobra acumulada)
        for g in grupos:
            for j,col in enumerate(cols[g]):
                tt=sum(col[1])
                if tt>1: tv.append(y[g][j]); tc.append(int(round(tt)))
        if tv: md.Add(cp_model.LinearExpr.WeightedSum(tv,tc) <= int(round(_teto_j)))
        return md,y

    sel_final={}
    def _extrai(sv,y):
        plano={oid:None for oid in cen.obras}
        sel_final.clear()
        for g in grupos:
            for j,yv in enumerate(y[g]):
                if sv.Value(yv)==1:
                    sel_final[g]=j
                    for oid,v in cols[g][j][2].items():
                        if v is not None: plano[oid]=v
        return plano
    def _termos(y,idx):
        V=[];C=[]
        for g in grupos:
            for j,col in enumerate(cols[g]):
                c=col[idx]; keep=(c>0) if idx in (4,5) else (abs(c)>0.5)
                if keep: V.append(y[g][j]); C.append(int(round(c)))
        return V,C
    def _obrig_terms(y):
        # nº de obras obrigatorias construidas = soma (nas cidades com obrigatoria) de
        # obrig_por_cidade[g] * (coluna escolhida != 'nada')
        V=[];C=[]
        for g in grupos:
            if g not in obrig_cid: continue
            cnt=obrig_por_cidade[g]; ni=nada_idx.get(g)
            for j in range(len(cols[g])):
                if j==ni: continue
                V.append(y[g][j]); C.append(cnt)
        return V,C
    def _obrig_floor(md,y,O0):
        V,C=_obrig_terms(y)
        if V and O0>0: md.Add(cp_model.LinearExpr.WeightedSum(V,C) >= int(O0))

    _foco=getattr(cen,"foco_cobertura",None); _modo=str(getattr(cen,"penalidade_cobertura","meta+cobertura")).lower()

    def _sv(fracao):
        """Solver de uma fase. Fabrica UNICA para o `gap_relativo` valer em todas elas — antes
        cada fase montava o seu, e um criterio novo teria de ser repetido em cinco lugares,
        que e como se esquece um."""
        s=cp_model.CpSolver()
        s.parameters.max_time_in_seconds=max(5.0,float(max_time_s)*float(fracao))
        s.parameters.num_search_workers=int(workers)
        if gap_relativo and gap_relativo>0: s.parameters.relative_gap_limit=float(gap_relativo)
        return s

    def _fase0_obrig():
        """FASE 0: maximiza o nº de obras OBRIGATORIAS construidas respeitando o teto anual."""
        md,y=_base(); V,C=_obrig_terms(y)
        if not V: return 0
        md.Maximize(cp_model.LinearExpr.WeightedSum(V,C))
        sv=_sv(0.35)
        st=sv.Solve(md)
        return int(round(sv.ObjectiveValue())) if st in (cp_model.OPTIMAL,cp_model.FEASIBLE) else 0

    def _run():
        O0=_fase0_obrig()                                   # FASE 0: obrigatorias primeiro
        if _foco is not None and _foco>=0.95:               # lexicografico
            if _modo=="ligacao":
                md,y=_base(); _obrig_floor(md,y,O0); OV,OC=_termos(y,5); md.Maximize(cp_model.LinearExpr.WeightedSum(OV,OC))
                sv=_sv(1.0)
                st=sv.Solve(md); return _extrai(sv,y),st,"-",5,O0
            md1,y1=_base(); _obrig_floor(md1,y1,O0); MV,MC=_termos(y1,4)
            md1.Minimize(cp_model.LinearExpr.WeightedSum(MV,MC) if MV else 0)
            s1=_sv(0.4)
            st1=s1.Solve(md1)
            if st1==cp_model.INFEASIBLE: return None,st1,None,None,O0
            Mstar=int(round(s1.ObjectiveValue())) if MV else 0
            md2,y2=_base(); _obrig_floor(md2,y2,O0); MV2,MC2=_termos(y2,4)
            if MV2: md2.Add(cp_model.LinearExpr.WeightedSum(MV2,MC2) <= Mstar)
            _idx2=3 if _modo=="meta" else 5
            OV,OC=_termos(y2,_idx2); md2.Maximize(cp_model.LinearExpr.WeightedSum(OV,OC))
            sv=_sv(0.6)
            st=sv.Solve(md2)
            if st==cp_model.INFEASIBLE: return None,st,Mstar,_idx2,O0
            plano2=_extrai(sv,y2)                            # o plano da fase 2, se a 3 falhar

            # ---------------- FASE 3: DESEMPATE POR RETORNO ----------------
            # Sem ela o VPL e SORTEIO. A fase 2 maximiza cobertura; ao chegar em C* devolve o
            # primeiro plano que o atinge, e entre um que rende 154 Mi e outro que rende 118 Mi
            # com a MESMA cobertura o solver nao tem preferencia. Qual dos dois sai depende da
            # ordem de busca e do timing das threads do portfolio paralelo.
            #
            # Medido na uA3 (67 cidades, 120 Mi/ano): duas execucoes com parametros IDENTICOS
            # devolveram VPL de 154,89 Mi e 150,27 Mi. Isso nao e vies — vies preservaria a
            # ordem entre cenarios. E DISPERSAO, que embaralha. Para quem usa o otimizador para
            # COMPARAR planos, e o defeito que importa: dois cenarios so sao distinguiveis se a
            # diferenca entre eles superar a dispersao.
            #
            # SEMPRE VIAVEL: o plano da fase 2 satisfaz `>= Cstar`. O teste de status existe
            # para o caso de ela nao terminar a tempo — devolve o plano da fase 2, nunca pior
            # que o comportamento anterior.
            #
            # SO quando a fase 2 otimizou COBERTURA (idx 5). No modo "meta" ela ja maximiza o
            # proprio VPL (idx 3), e desempatar VPL por VPL seria gastar tempo para nada.
            if _idx2==5:
                Cstar=int(round(sv.ObjectiveValue()))
                md3,y3=_base(); _obrig_floor(md3,y3,O0)
                MV3,MC3=_termos(y3,4)
                if MV3: md3.Add(cp_model.LinearExpr.WeightedSum(MV3,MC3) <= Mstar)
                CV,CC=_termos(y3,5)
                if CV: md3.Add(cp_model.LinearExpr.WeightedSum(CV,CC) >= Cstar)
                RV,RC=_termos(y3,0)                          # idx 0 = VPL de objetivo
                if RV:
                    md3.Maximize(cp_model.LinearExpr.WeightedSum(RV,RC))
                    s3=_sv(0.6)
                    st3=s3.Solve(md3)
                    if st3 in (cp_model.OPTIMAL,cp_model.FEASIBLE):
                        if verbose: print(f"  [info] desempate por retorno: cobertura travada em {Cstar}")
                        return _extrai(s3,y3),st3,Mstar,_idx2,O0
            return plano2,st,Mstar,_idx2,O0
        # ponderado
        md,y=_base(); _obrig_floor(md,y,O0); OV,OC=_termos(y,0); md.Maximize(cp_model.LinearExpr.WeightedSum(OV,OC))
        sv=_sv(1.0)
        st=sv.Solve(md)
        if st==cp_model.INFEASIBLE: return None,st,None,0,O0
        return _extrai(sv,y),st,None,0,O0

    plano,st,Mstar,idx2,O0=_run()
    if plano is None: plano={oid:None for oid in cen.obras}     # guarda: nunca deixa avaliar quebrar
    res=M.avaliar(cen,plano)

    # ---------------- AUDITORIA + REPARO DO TETO ANUAL ----------------
    # O master ja restringe o CAPEX por ano usando o PERFIL de cada coluna, entao um plano viavel
    # NAO pode estourar o teto. Se estourar, ha divergencia entre o perfil da coluna e o CAPEX que
    # ela realmente gera (arquivos fora de versao, ou a celula de carga do cenario reexecutada
    # depois do solve). Em vez de devolver um plano inviavel em silencio: auditamos, avisamos e
    # REPARAMOS trocando colunas ate o teto ser respeitado (a coluna 'nada' sempre existe, entao a
    # viabilidade e sempre alcancavel).
    ok,viol=M.auditar_orcamento(cen,res)
    res["auditoria_orcamento"]={"ok":ok,"violacoes":viol,"reparos":[]}
    if not ok and sel_final:
        if verbose:
            print(f"  [ALERTA] o plano estourou o teto anual em {len(viol)} ano(s) - reparando:")
            for (ac_,iy,gs,tt,ex) in sorted(viol,key=lambda t:-t[4])[:5]:
                print(f"      {ac_}: gasto R$ {gs:,.0f} > teto R$ {tt:,.0f}   (excesso R$ {ex:,.0f})")
        orcv=cen.orc[reg]; _ac=int(getattr(cen,"anos_capex",anos))
        _tetoj=cen.orc_total if getattr(cen,"orc_total",None) else sum(orcv[:_ac])
        def _exc(t):
            _ann=sum(max(0.0,t[Y]-(orcv[Y] if Y<len(orcv) else 0.0)) for Y in range(min(_ac,len(t))))
            return _ann+max(0.0,sum(t)-_tetoj)
        sel=dict(sel_final)
        tot=[0.0]*anos
        for g,j in sel.items():
            pf=cols[g][j][1]
            for Y in range(anos): tot[Y]+=(pf[Y] if Y<len(pf) else 0.0)
        E=_exc(tot); rep=[]
        while E>1.0 and len(rep)<200:
            best=None
            for g in grupos:
                cur=cols[g][sel[g]][1]
                for j,c in enumerate(cols[g]):
                    if j==sel[g]: continue
                    nt=[tot[Y]-cur[Y]+c[1][Y] for Y in range(anos)]
                    e2=_exc(nt)
                    if e2 < E-1e-6:                      # so trocas que REDUZEM o excesso
                        cand=(E-e2, c[5]-cols[g][sel[g]][5], g, j, nt)   # 1o reduz mais; 2o perde menos cobertura
                        if best is None or (cand[0],cand[1])>(best[0],best[1]): best=cand
            if best is None: break
            _,_,g,j,nt=best
            rep.append({"cidade":g,"de":sel[g],"para":j,
                        "capex_total_antes":sum(cols[g][sel[g]][1]),"capex_total_depois":sum(cols[g][j][1])})
            sel[g]=j; tot=nt; E=_exc(tot)
        plano={oid:None for oid in cen.obras}
        for g,j in sel.items():
            for oid,v in cols[g][j][2].items():
                if v is not None: plano[oid]=v
        res=M.avaliar(cen,plano)
        ok,viol=M.auditar_orcamento(cen,res)
        res["auditoria_orcamento"]={"ok":ok,"violacoes":viol,"reparos":rep}
        if verbose:
            print(f"  [reparo] {len(rep)} troca(s) de coluna | teto respeitado: {'SIM' if ok else 'NAO'} "
                  f"| VPL apos reparo: R$ {res['vpl']:,.0f}")
    # ------------------------------------------------------------------
    ob=[oid for oid,o in cen.obras.items() if o.obrigatoria and o.eh_aegea()]
    nao=[oid for oid in ob if res["plano"].get(oid) is None]
    res["obrig_total"]=len(ob); res["obrig_construidas"]=len(ob)-len(nao); res["obrig_nao_construidas"]=nao
    aviso=""
    if nao:
        aviso=(f"ORCAMENTO INSUFICIENTE: {len(nao)} de {len(ob)} obras obrigatorias NAO couberam no teto anual "
               f"(as {len(ob)-len(nao)} que couberam foram priorizadas ANTES de qualquer obra opcional). "
               f"Ex.: {nao[:8]}"+(" ..." if len(nao)>8 else ""))
        if verbose: print("  [aviso] "+aviso)
    res["aviso_obrigatoria"]=aviso
    res["obrig_desconsideradas_fora_janela"]=getattr(cen,"_obrig_desconsideradas",[])
    if not res.get("auditoria_orcamento",{}).get("ok",True):
        res["aviso_orcamento"]=("PLANO AINDA ESTOURA O TETO apos o reparo - confira se engine, solver e banco "
                                "estao na mesma versao e se a celula de carga nao foi reexecutada depois do solve.")
    _p2="cobertura" if idx2==5 else ("VPL" if idx2==3 else "-")
    _ob_tag=f" | obrig {len(ob)-len(nao)}/{len(ob)}"
    if _foco is not None and _foco>=0.95:
        res["milp_status"]=("OTIMO" if st==cp_model.OPTIMAL else "VIAVEL(limite de tempo)")+_ob_tag+(f" | lexicografico: min metas_nao={Mstar}, 2a prior={_p2}" if _modo!="ligacao" else " | so cobertura")
        res["vpl_obj"]=res["vpl"]; res["milp_bound"]=res["vpl"] if st==cp_model.OPTIMAL else res.get("milp_bound",res["vpl"])
        res["milp_solver"]="CP-SAT lexicografico por cidade (obrigatorias primeiro)"
    else:
        res["milp_status"]=("OTIMO" if st==cp_model.OPTIMAL else "VIAVEL(limite de tempo)")+_ob_tag
        res["milp_bound"]=res.get("milp_bound",res["vpl"]); res["milp_solver"]="CP-SAT (ponderado por cidade, obrigatorias primeiro)"
    return res
