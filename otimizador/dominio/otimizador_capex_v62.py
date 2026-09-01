# -*- coding: utf-8 -*-
"""
OTIMIZADOR DE PRIORIZACAO DE CAPEX - ESGOTO (v8)

CAPEX POR COMPONENTE (entrada):
  COLETA (unidade por sub-bacia): capex_ligacao + capex_rede  (Ligacao pode ter capex ~0)
  TRANSPORTE (por sub-bacia/no):  capex_tronco + capex_eee + capex_lr
  ETE (por sistema):              capex_ete
O CAPEX de cada obra e a soma dos capex dos seus componentes.

Classificacao por obra (capex total x prazo_exec):
  capex>0                -> AEGEA (investe e executa; leva prazo).
  capex=0 e prazo>0      -> TERCEIRO (ex.: prefeitura): nao investe, mas leva prazo.
  capex=0 e prazo=0      -> NAO NECESSARIA (ja pronta).

FATURAMENTO de uma sub-bacia (revenue) so comeca quando TODAS as obras necessarias estao PRONTAS:
  a COLETA da sub-bacia + os TRANSPORTES de todos os nos do caminho ate a ETE + a ETE do sistema.
  Receita comeca em (ultima a ficar pronta + lag), com rampa de maturacao.
Objetivo: max VPL. Cobertura por cidade conta so ligacoes tratadas. Orcamento/paralelo: so Aegea.
Le planilha Excel (abas: Componentes, Parametros, Cidades, Nos, Obras_Coleta, Obras_Transporte, Obras_ETE, Orcamento).
"""
from itertools import product
import math

class No:
    def __init__(self,id,cidade,sistema,regional,jusante):
        self.id=id;self.cidade=cidade;self.sistema=sistema;self.regional=regional;self.jusante=jusante
class Cidade:
    def __init__(self,id,cob_atual,universo,meta_aumento,obras_paralelo=99):
        self.id=id;self.cob_atual=float(cob_atual);self.universo=float(universo)
        self.meta_aumento=float(meta_aumento);self.obras_paralelo=int(obras_paralelo)
class Obra:
    def __init__(self,id,tipo,no=None,sistema=None,capex_comp=None,opex_ano=0,prazo_exec=0,
                 prazo_inicio=0,obrigatoria=False,proibida_ate=0,ligacoes=0,ticket_mes=0,preco_ligacao=0,
                 arrec_dir=1.0,arrec_ind=1.0,lag=1,maturacao=2,wacc=None,ligacoes_cobertura=None):
        self.id=id;self.tipo=tipo;self.no=no;self.sistema=sistema
        self.capex_comp={k:float(v or 0) for k,v in (capex_comp or {}).items()}
        self.capex=sum(self.capex_comp.values());self.opex_ano=float(opex_ano or 0)
        self.prazo=int(round(float(prazo_exec or 0)))
        self.obrig=int(round(float(obrigatoria or 0)))          # 0=nao; -1=obrigatoria em QUALQUER ano; N>=1=ano-plano EXATO (1-based)
        self.obrigatoria=(self.obrig!=0)
        self._obrig_planyear=(self.obrig if self.obrig>=1 else None)   # janela de ano-plano exato; None = qualquer ano
        self.proibida_ate=int(round(float(proibida_ate or 0)))  # 0=sem; -1=proibida SEMPRE (nunca); N>=1=ano-plano, nao antes de N
        self.proibida_nunca=(self.proibida_ate==-1)
        self.prazo_inicio=int(round(float(prazo_inicio or 0)))  # duracao de licenca/mobilizacao (meses)
        _proib=max(0,self.proibida_ate-1)*12 if self.proibida_ate>=1 else 0   # 'nao antes do ano-plano N' -> mes interno
        self.inicio_min=(10**7 if self.proibida_nunca else max(int(self.prazo_inicio),_proib))   # MES interno mais cedo de inicio
        # CAPEX = quantidade x preco unitario (quando o banco informa os dois)
        self.quantidade=None; self.preco_unitario=None; self.unidade=None
        self.lig=float(ligacoes or 0);self.ticket_mes=float(ticket_mes or 0);self.preco_ligacao=float(preco_ligacao or 0)
        # DUAS QUANTIDADES, e nao uma. `lig` e o que a obra HABILITA — dela sai a
        # receita, e ela e sempre o TOTAL. `lig_cob` e o que a obra conta para a
        # COBERTURA, que pode ser so a parcela residencial.
        #
        # Antes eram a mesma coisa, e por isso o recorte residencial nao tinha como
        # existir sem mexer no VPL: subtrair industria de `lig` derrubava receita
        # junto. Sao numeros de moedas diferentes — um e faturamento, o outro e
        # meta contratual — e passaram a ser dois campos.
        #
        # `None` (o caso normal) faz os dois iguais: sem recorte, cobertura e
        # receita contam as mesmas ligacoes.
        self.lig_cob=float(ligacoes_cobertura) if ligacoes_cobertura not in (None,"") else self.lig
        self.arrec_dir=float(arrec_dir or 1);self.arrec_ind=float(arrec_ind or 1)
        self.lag=int(lag or 1);self.mat=int(maturacao or 2)
        self.wacc=(float(wacc) if wacc not in (None,"") else None)   # WACC do componente (custo de capital); None -> usa taxa da regional
        self.wacc_origem="proprio"          # "proprio" (financiamento) | "wacc_medio" (unidade) | "ausente"
        self.necessaria=(self.capex>1e-9) or (self.prazo>0)
        self.responsavel="Aegea" if self.capex>1e-9 else ("Terceiro" if self.prazo>0 else "-")
        if self.responsavel=="Terceiro": self.opex_ano=0.0      # OPEX de terceiro nao e pago pela Aegea
    def eh_aegea(self):return self.responsavel=="Aegea"
    def receita_dir_regime(self):return self.ticket_mes*12.0*self.arrec_dir*self.lig
    def receita_ind_total(self): return self.preco_ligacao*self.arrec_ind*self.lig
class Cenario:
    def __init__(self,nos,cidades,obras,orc_anual,anos=20,hz=None):
        self.nos={n.id:n for n in nos};self.cidades={c.id:c for c in cidades};self.obras={o.id:o for o in obras}
        self.regionais=sorted({n.regional for n in nos})
        self.coletas=[o for o in obras if o.tipo=="coleta"]
        self.ete_do_sistema={}
        for o in obras:
            if o.tipo=="ete": self.ete_do_sistema[o.sistema]=o
        self.sistemas=sorted({n.sistema for n in nos}|{o.sistema for o in obras if o.tipo=="ete"})
        self.hz={sname:anos for sname in self.sistemas} if hz is None else dict(hz)  # horizonte (anos) por SISTEMA
        # NAO existe taxa de desconto por regional/unidade: cada elemento traz o SEU WACC
        # (coluna 'wacc' do banco) e e ele que desconta CAPEX, OPEX e receita.
        self.anos=int(max(self.hz.values())) if self.hz else anos
        self.orc={reg:(list(v) if isinstance(v,(list,tuple)) else [float(v)]*self.anos) for reg,v in orc_anual.items()}
    def regional_de_no(self,no): return self.nos[no].regional if no in self.nos else self.regionais[0]
    def regional_da(self,o):
        if o.tipo in ("ete","ete_mod"):
            algum=next((n for n in self.nos.values() if n.sistema==o.sistema),None)
            return algum.regional if algum else self.regionais[0]
        return self.nos[o.no].regional
    def cidade_da(self,o):
        if o.tipo in ("ete","ete_mod"):
            algum=next((n for n in self.nos.values() if n.sistema==o.sistema),None)
            return algum.cidade if algum else list(self.cidades)[0]
        return self.nos[o.no].cidade
    def sistema_de(self,o): return o.sistema if o.tipo in ("ete","ete_mod") else self.nos[o.no].sistema
    def horizonte(self,o): return int(self.hz.get(self.sistema_de(o), self.anos))
    def taxa_de(self,o):
        w=getattr(o,"wacc",None)
        if w is None:                                        # SEM WACC PADRAO: todo elemento deve ter o seu
            raise ValueError("WACC ausente para o elemento '%s' (%s). O desconto e SEMPRE por elemento: "
                             "preencha a coluna 'wacc' desse componente no banco."%(getattr(o,'id','?'),getattr(o,'tipo','?')))
        return float(w)

def capex_unitario_txt(o, curto=False):
    """'12,0 m x R$ 450,00/m = R$ 5.400' — vazio se o banco nao trouxe os unitarios."""
    q=getattr(o,"quantidade",None); p=getattr(o,"preco_unitario",None)
    if q is None or p is None: return ""
    u=getattr(o,"unidade",None) or "un"
    if curto: return f"{q:,.1f} {u} x R$ {p:,.2f}"
    return f"{q:,.1f} {u} x R$ {p:,.2f}/{u} = R$ {q*p:,.0f}"


# ---- PERFIL TEMPORAL DO OPEX (fixo no codigo) --------------------------------
# O 'opex' da base e o OPEX ESTAVEL (plato). Ao iniciar a ARRECADACAO o OPEX entra
# ABAIXO do plato e cresce de forma CONCAVA (rapido no comeco, desacelerando) ate
# estabilizar no plato apos _OPEX_ANOS_ESTAB anos. Nunca ultrapassa o plato.
_OPEX_FRAC_INICIAL = 0.5     # fracao do OPEX (maximo) no inicio da arrecadacao
def _opex_mult(meses, mat):
    """Multiplicador do OPEX a 'meses' do inicio da arrecadacao. Sobe de forma CONCAVA
    (rapido no inicio, desacelerando) e atinge o MAXIMO no MESMO periodo de RAMPUP da
    receita — a maturacao da obra (mat meses = tempo ate o maximo de arrecadacao).
    1.0 = OPEX dado na base (todas as ligacoes faturando)."""
    if mat is None or mat <= 1 or meses >= mat: return 1.0
    if meses <= 0: return _OPEX_FRAC_INICIAL
    x = meses / float(mat); g = 1.0 - (1.0 - x)**2           # ease-out concavo (rapido no inicio)
    return _OPEX_FRAC_INICIAL + (1.0 - _OPEX_FRAC_INICIAL) * g


def caminho(cen,no):
    seg=[];cur=no;g=0
    while cur!="ETE" and cur in cen.nos and g<200: seg.append(cur);cur=cen.nos[cur].jusante;g+=1
    return seg

def requisitos(cen,lig):
    """Obras NECESSARIAS para a sub-bacia da Ligacao faturar (componentes DESACOPLADOS):
    a propria Ligacao + a Rede da sub-bacia + os componentes de TRANSPORTE (Tronco/EEE/LR)
    de todos os nos do caminho ate a ETE + a ETE do sistema."""
    X=lig.no
    reqs=[lig]
    reqs+=[o for o in cen.obras.values() if o.tipo=="rede" and o.no==X]
    for n in caminho(cen,X):
        reqs+=[o for o in cen.obras.values() if o.tipo=="transporte" and o.no==n]
    sis=cen.nos[X].sistema
    if sis in cen.ete_do_sistema: reqs.append(cen.ete_do_sistema[sis])
    return [o for o in reqs if o.necessaria]

def _reqs_sem_ete(cen,sb):
    reqs=[o for o in cen.obras.values() if o.tipo in ("coleta","rede") and o.no==sb]
    for n in caminho(cen,sb):
        reqs+=[o for o in cen.obras.values() if o.tipo=="transporte" and o.no==n]
    return [o for o in reqs if o.necessaria]
def _conectada(cen,sb,plano):
    rr=_reqs_sem_ete(cen,sb)
    if not any(o.tipo=="coleta" for o in rr): return False
    for r in rr:
        if r.eh_aegea() and plano.get(r.id) is None: return False
    return True
def _dimensiona_etes(cen,plano):
    """Define CAPEX/necessidade de cada ETE a partir da VAZAO das sub-bacias servidas (marginal),
    em modulos: n_mod = teto(max(0, vazao_nova - folga)/cap_modulo)."""
    dem={}
    for sb in cen.nos:
        if _conectada(cen,sb,plano):
            sis=cen.nos[sb].sistema; dem[sis]=dem.get(sis,0.0)+cen.vazao.get(sb,0.0)
    for sis,e in cen.ete_do_sistema.items():
        d=dem.get(sis,0.0); e.demanda=d; e.cap_max=None
        if getattr(cen,"ete_faseada",False):              # ETE FASEADA: modulos crescem com o fluxo (custo em avaliar)
            e.n_mod=0; e.necessaria=False; e.capex=0.0; e.capex_comp={}; e.responsavel="-"
            continue
        if getattr(cen,"ete_fixo",False):                 # MODO ETE FIXA: capex dado, sem modulos/capacidade
            e.n_mod=0; e.necessaria=(d>1e-9)
            e.capex=e.capex_fixo if e.necessaria else 0.0
            e.capex_comp={"ETE (capex fixo)":e.capex} if e.necessaria else {}
            e.responsavel="Aegea" if e.capex>1e-9 else "-"
            continue
        if getattr(e,"nova",False):                             # ETE NOVA (pacote unico: terreno + modulos dados)
            e.n_mod=e.modulos
            e.cap_max=e.modulos*e.cap_modulo                    # TETO de vazao
            e.necessaria=(d>1e-9)                               # qualquer vazao conectada exige a ETE nova
            cap=(e.capex_terreno+e.modulos*e.capex_modulo) if e.necessaria else 0.0
            e.capex=cap
            e.capex_comp={f"ETE nova: terreno + {e.modulos} mod":cap} if e.necessaria else {}
        else:                                                   # EXPANSAO (calcula modulos pela vazao)
            exc=max(0.0,d-e.folga)
            n=int(math.ceil(exc/e.cap_modulo)) if (exc>1e-9 and e.cap_modulo>0) else (1 if exc>1e-9 else 0)
            e.n_mod=n; cap=n*e.capex_modulo
            e.capex=cap; e.necessaria=(n>0)
            e.capex_comp={f"ETE {n} mod":cap} if n>0 else {}
        e.responsavel="Aegea" if e.capex>1e-9 else "-"
    return dem

# ---------- economia (MENSAL: prazos/lag/maturidade em MESES; desconto/agregacao ANUAL via ano=mes//12)
def _ready(cen,o,plano):
    if not o.necessaria: return 0
    if o.responsavel=="Terceiro": return o.prazo                 # meses de execucao
    y=plano.get(o.id); return (y+o.prazo) if y is not None else None   # y=mes de inicio; prazo em meses
_FORMA_ADOCAO = "scurve"        # 'linear' | 'scurve' — curva de ADESAO ao longo da maturacao

def _set_forma_adocao(f):
    global _FORMA_ADOCAO
    _FORMA_ADOCAO = "linear" if str(f).strip().lower().startswith("lin") else "scurve"
    return _FORMA_ADOCAO

def _rampa(k,mat,forma=None):
    """Fracao ACUMULADA de adesao no mes k (0-based), ao longo de 'mat' meses de maturacao.
    linear: adesao constante mes a mes (reta 0->1).
    scurve: curva em S (smoothstep) — adesao lenta no comeco, PICO no meio, lenta no fim;
            a derivada (novos adotantes/mes) e um sino, como uma curva normal de adocao."""
    if k<0: return 0.0
    if mat<=1: return 1.0
    t=min(1.0,(k+1)/float(mat))                      # posicao no periodo de maturacao [0,1]
    if (forma or _FORMA_ADOCAO)=="linear": return t
    return t*t*(3.0-2.0*t)                           # smoothstep: 0 e 1 exatos nas pontas
def _pv_custo(cen,o,start):
    tx=cen.taxa_de(o);Hm=cen.horizonte(o)*12;pe=o.prazo;v=0.0
    if pe>0:
        cm=o.capex/pe                                            # CAPEX espalhado pelos meses de execucao
        for m in range(start,min(start+pe,Hm)): v-=cm/(1.0+tx)**(m//12)
    elif start<Hm:
        v-=o.capex/(1.0+tx)**(start//12)
    return v   # OPEX nao entra aqui: caminha com a RECEITA (ver avaliar)
def _vazao_por_obra(cen):
    """VAZAO total das sub-bacias (coletas) que EXIGEM cada obra — topologico, independe de
    plano/necessaria. Base para RATEAR por vazao o CAPEX de obras COMPARTILHADAS (transporte a
    jusante e a ETE), evitando super-ponderar a mesma obra no WACC de varias sub-bacias.
    vpo[r.id] = Sum(vazao das sub-bacias cujo caminho ate a ETE exige r)."""
    m=getattr(cen,"_vazao_por_obra_cache",None)
    if m is not None: return m
    by_rede={}; by_transp={}
    for o in cen.obras.values():
        if o.tipo=="rede": by_rede.setdefault(o.no,[]).append(o.id)
        elif o.tipo=="transporte": by_transp.setdefault(o.no,[]).append(o.id)
    m={}
    for c in cen.coletas:
        vz=cen.vazao.get(c.no,0.0); X=c.no
        ids=[c.id]+by_rede.get(X,[])
        for n in caminho(cen,X): ids+=by_transp.get(n,[])
        sis=cen.nos[X].sistema
        if sis in cen.ete_do_sistema: ids.append(cen.ete_do_sistema[sis].id)   # ETE: compartilhada por todo o sistema
        for rid in ids: m[rid]=m.get(rid,0.0)+vz
    cen._vazao_por_obra_cache=m
    return m

def _ete_share(cen):
    """CAPEX da ETE (modulos de EXPANSAO) rateado por sub-bacia, para pesar no WACC da receita.
    Regra da FOLGA: a capacidade ociosa e consumida pelas sub-bacias de MENOR VAZAO primeiro (elas
    NAO pagam a ETE); so o fluxo ACIMA da folga rateia os modulos novos. Plano-independente (usa o
    POTENCIAL cheio: todas as sub-bacias do sistema), para o solver ter coeficientes fixos.
    Retorna {sub-bacia: (capex_ete_rateado, wacc_ete)}; a soma dos rateios = CAPEX total da ETE."""
    m=getattr(cen,"_ete_share_cache",None)
    if m is not None: return m
    m={}; syssub={}
    for sb,no in cen.nos.items(): syssub.setdefault(no.sistema,[]).append(sb)
    for sis,subs in syssub.items():
        e=cen.ete_do_sistema.get(sis)
        if e is None: continue
        wacc_e=getattr(e,"wacc",None)
        if wacc_e is None: continue
        vz={sb:cen.vazao.get(sb,0.0) for sb in subs}; Sv=sum(vz.values())
        if Sv<=0: continue
        cap_mod=getattr(e,"cap_modulo",0.0) or 0.0; capex_mod=getattr(e,"capex_modulo",0.0) or 0.0
        if getattr(e,"nova",False):
            folga=0.0; capex_tot=getattr(e,"capex_terreno",0.0)+getattr(e,"modulos",0)*capex_mod
        else:
            folga=getattr(e,"folga",0.0) or 0.0
            _exc=max(0.0,Sv-folga)
            n=int(math.ceil(_exc/cap_mod)) if (_exc>1e-9 and cap_mod>0) else (1 if _exc>1e-9 else 0)
            capex_tot=n*capex_mod
        excess=max(0.0,Sv-folga)
        if excess<=1e-9 or capex_tot<=0:                 # tudo cabe na folga -> ETE nao pesa
            for sb in subs: m[sb]=(0.0,wacc_e)
            continue
        cum=0.0
        for sb in sorted(subs,key=lambda s:vz[s]):        # MENOR VAZAO PRIMEIRO consome a folga
            v=vz[sb]; hi=cum+v
            pay=min(v,max(0.0, hi-max(cum,folga)))        # parte de v ACIMA da folga
            m[sb]=(capex_tot*(pay/excess), wacc_e); cum=hi
    cen._ete_share_cache=m
    return m

def _wacc_receita(cen,o):
    """WACC que desconta a RECEITA de uma sub-bacia = media dos WACCs das obras NECESSARIAS,
    ponderada pelo CAPEX RATEADO POR VAZAO. Transporte compartilhado a jusante: fatia = vazao(o)/
    Sum(vazao das que exigem a obra). ETE: entra pelos MODULOS DE EXPANSAO (v30), rateados com a
    folga consumida por MENOR VAZAO primeiro (ver _ete_share). Obra local -> CAPEX cheio.
    Terceiros (capex 0) nao pesam. Sem CAPEX na cadeia -> WACC da propria ligacao."""
    vpo=_vazao_por_obra(cen); vz_o=cen.vazao.get(o.no,0.0)
    num=den=0.0
    for r in requisitos(cen,o):
        if getattr(r,"tipo","")=="ete": continue          # ETE tratada a parte (com folga) em _ete_share
        cx=getattr(r,"capex",0.0) or 0.0
        if cx<=0: continue
        tot=vpo.get(r.id,0.0)
        frac=(vz_o/tot) if tot>0 else 1.0                 # obra local: tot==vz_o -> frac=1 (CAPEX cheio)
        cxa=cx*frac; num+=cxa*cen.taxa_de(r); den+=cxa
    esh=_ete_share(cen).get(o.no)                          # fatia da ETE (modulos de expansao, com folga)
    if esh and esh[0]>0: num+=esh[0]*esh[1]; den+=esh[0]
    return (num/den) if den>0 else cen.taxa_de(o)
def _fator_esgoto_ano(cen,o):
    """Fator de equivalencia ESGOTO/AGUA por ANO (indice interno) da cidade da sub-bacia.
    O ticket_medio do banco e da AGUA; a tarifa de esgoto = ticket_agua * fator (tipicamente 0,8-1,0).
    A COBERTURA de cada ano vem de uma trajetoria EXOGENA: cobertura ATUAL (base_lig/max_lig) ->
    metas contratuais (aba metas-cobertura), interpoladas linearmente e mantidas apos a ultima meta.
    O fator sai da TABELA DE FAIXAS (aba 'fator-esgoto'): a maior faixa com cobertura_pct <= cobertura.
    Cidade sem tabela -> 1.0 (comportamento anterior, ticket ja seria de esgoto)."""
    cache=getattr(cen,"_fator_esg_cache",None)
    if cache is None: cache={}; cen._fator_esg_cache=cache
    cid=cen.cidade_da(o)
    if cid in cache: return cache[cid]
    anos=int(cen.anos)
    faixas=sorted((getattr(cen,"fator_esgoto",{}) or {}).get(cid,[]))
    if not faixas:
        out=[1.0]*anos; cache[cid]=out; return out
    mx=float((getattr(cen,"max_lig",{}) or {}).get(cid,0.0) or 0.0)
    bs=float((getattr(cen,"base_lig",{}) or {}).get(cid,0.0) or 0.0)
    ab=int((getattr(cen,"ano_base",{}) or {}).get(cid,0) or 0)
    pts=[(0,(bs/mx) if mx>0 else 0.0)]                      # ano 0 = cobertura ATUAL
    for _a,_p in sorted((getattr(cen,"metas_cobertura",{}) or {}).get(cid,{}).items()):
        _i=int(_a)-ab
        if _i>0: pts.append((_i,float(_p)))
    pts=sorted(set(pts))
    def _cob(Y):                                            # cobertura exogena no ano Y (interpolada)
        if Y<=pts[0][0]: return pts[0][1]
        if Y>=pts[-1][0]: return pts[-1][1]
        for i in range(1,len(pts)):
            x0,y0=pts[i-1]; x1,y1=pts[i]
            if Y<=x1: return y0+(y1-y0)*((Y-x0)/max(1,(x1-x0)))
        return pts[-1][1]
    def _fat(cb):                                           # maior faixa cuja cobertura <= cb
        f=faixas[0][1]
        for c,fv in faixas:
            if cb+1e-9>=c: f=fv
            else: break
        return f
    out=[_fat(_cob(Y)) for Y in range(anos)]
    cache[cid]=out; return out

def _pv_receita(cen,o,ini,fat=None):
    """fat = lista do fator esgoto/agua por ANO (ENDOGENO, da cobertura realizada do plano).
    Se None, cai no fator EXOGENO pelas metas (_fator_esgoto_ano) - usado pelo backend MILP."""
    tx=_wacc_receita(cen,o);Hm=cen.horizonte(o)*12
    rdm=o.receita_dir_regime()/12.0;ri=o.receita_ind_total();v=0.0
    fe=fat if fat is not None else _fator_esgoto_ano(cen,o); nf=len(fe)
    for m in range(Hm):
        k=m-ini; Y=m//12
        f=(fe[Y] if Y<nf else (fe[-1] if nf else 1.0))
        r=rdm*_rampa(k,o.mat)*f + ri*max(0.0,_rampa(k,o.mat)-_rampa(k-1,o.mat))   # fator SO na tarifa recorrente
        if r: v+=r/(1.0+tx)**Y
    return v

def _faixa_fator(fx,cb):
    """Maior faixa cuja cobertura_pct <= cobertura. fx = [(cob_fracao, fator)] ordenado."""
    f=fx[0][1] if fx else 1.0
    for c,fv in fx:
        if cb+1e-9>=c: f=fv
        else: break
    return f

def _uf(cen,sb):
    """Fator que converte LIGACOES da sub-bacia para a UNIDADE DE COBERTURA da cidade
    (1.0 = ligacoes; densidade_economias = economias; densidade_populacao = populacao).
    Vale para a META e para a faixa de PARIDADE. A RECEITA continua sempre em ligacoes."""
    return float((getattr(cen,"unid_fator",{}) or {}).get(sb,1.0) or 1.0)

def _fator_por_cobertura_realizada(cen,elig,inicio,anos):
    """ENDOGENO: cobertura REALIZADA do plano (base + ligacoes que ja faturam no ano) -> faixa -> fator.
    Como a decomposicao e por CIDADE e cada coluna e um plano completo da cidade, isso e EXATO:
    cruzar uma faixa passa a valer no VPL daquela coluna (vira incentivo, sem linearizacao)."""
    maxl=getattr(cen,"max_lig",{}) or {}; basel=getattr(cen,"base_lig",{}) or {}
    fall=getattr(cen,"fator_esgoto",{}) or {}
    out={}
    for cid in set(list(maxl)+list(basel)):
        fx=sorted(fall.get(cid,[]))
        if not fx: out[cid]=[1.0]*anos; continue
        mx=float(maxl.get(cid,0.0) or 0.0)
        arr=[float(basel.get(cid,0.0) or 0.0)]*anos
        for o in cen.coletas:
            if not elig.get(o.id) or cen.cidade_da(o)!=cid: continue
            _fu=_uf(cen,o.no)
            for Y in range(max(0,inicio[o.id]//12),anos): arr[Y]+=o.lig_cob*_fu
        out[cid]=[_faixa_fator(fx,(arr[Y]/mx) if mx>0 else 0.0) for Y in range(anos)]
    return out

def _pv_efeito_base(cen,fat_cid):
    """EFEITO-BASE: ao cruzar uma faixa, TODAS as ligacoes JA EXISTENTES da cidade passam a pagar a
    nova equivalencia. Receita extra (ou perda) = ligacoes_atuais * ticket_agua * 12 * arrecadacao *
    (fator(ano) - fator INICIAL, o da cobertura ANTES das obras), descontada pelo WACC-receita da
    propria sub-bacia. Antes o modelo so contava as ligacoes NOVAS."""
    sr=getattr(cen,"sub_receita",{}) or {}
    if not sr or not fat_cid: return 0.0
    maxl=getattr(cen,"max_lig",{}) or {}; basel=getattr(cen,"base_lig",{}) or {}
    fall=getattr(cen,"fator_esgoto",{}) or {}
    f0={}
    for cid,fx in fall.items():
        fx=sorted(fx); mx=float(maxl.get(cid,0.0) or 0.0); bs=float(basel.get(cid,0.0) or 0.0)
        f0[cid]=_faixa_fator(fx,(bs/mx) if mx>0 else 0.0)     # referencia = cobertura ANTES das obras
    v=0.0
    for o in cen.coletas:
        cid=cen.cidade_da(o); fe=fat_cid.get(cid)
        if not fe: continue
        d=sr.get(o.no)
        if not d: continue
        base_ano=float(d.get("atuais",0.0) or 0.0)*float(d.get("ticket",0.0) or 0.0)*12.0*float(d.get("arrec",1.0) or 1.0)
        if base_ano<=0: continue
        tx=_wacc_receita(cen,o); fr=f0.get(cid,1.0); H=min(len(fe),cen.horizonte(o))
        for Y in range(H):
            df=fe[Y]-fr
            if abs(df)>1e-12: v+=base_ano*df/(1.0+tx)**Y
    return v

def _efeito_base_por_ano(cen,fat_cid,anos):
    """Efeito-base NOMINAL por ano (para EBITDA / fluxo de caixa). Mesma base do _pv_efeito_base,
    porem SEM desconto: base_ano * (fator(ano) - fator inicial)."""
    out=[0.0]*anos
    sr=getattr(cen,"sub_receita",{}) or {}
    if not sr or not fat_cid: return out
    maxl=getattr(cen,"max_lig",{}) or {}; basel=getattr(cen,"base_lig",{}) or {}
    fall=getattr(cen,"fator_esgoto",{}) or {}
    f0={}
    for cid,fx in fall.items():
        fx=sorted(fx); mx=float(maxl.get(cid,0.0) or 0.0); bs=float(basel.get(cid,0.0) or 0.0)
        f0[cid]=_faixa_fator(fx,(bs/mx) if mx>0 else 0.0)
    for o in cen.coletas:
        cid=cen.cidade_da(o); fe=fat_cid.get(cid)
        if not fe: continue
        d=sr.get(o.no)
        if not d: continue
        base_ano=float(d.get("atuais",0.0) or 0.0)*float(d.get("ticket",0.0) or 0.0)*12.0*float(d.get("arrec",1.0) or 1.0)
        if base_ano<=0: continue
        fr=f0.get(cid,1.0); H=min(len(fe),cen.horizonte(o),anos)
        for Y in range(H):
            out[Y]+=base_ano*(fe[Y]-fr)
    return out


def avaliar(cen,plano):
    _dimensiona_etes(cen,plano)
    ready={oid:_ready(cen,o,plano) for oid,o in cen.obras.items()}
    anos=cen.anos;vpl=0.0;capex_ano={reg:[0.0]*anos for reg in cen.regionais};opex_ano=[0.0]*anos
    for oid,o in cen.obras.items():
        if not o.necessaria: continue
        if o.eh_aegea():
            y=plano.get(oid)
            if y is None: continue
            vpl+=_pv_custo(cen,o,y)
            pe=o.prazo;reg=cen.regional_da(o)
            if pe>0:                                             # CAPEX por ANO (obra de 18m pega 2 anos)
                cm=o.capex/pe
                for m in range(y,y+pe):
                    if m//12<anos: capex_ano[reg][m//12]+=cm
            elif y//12<anos: capex_ano[reg][y//12]+=o.capex
        else:  # terceiro
            vpl+=_pv_custo(cen,o,0)
    elig={};motivo={};inicio={};chain_last={};need_o={}
    FAS=getattr(cen,"ete_faseada",False)
    for o in cen.coletas:
        if not o.necessaria: continue
        if o.eh_aegea() and plano.get(o.id) is None: continue
        reqs=requisitos(cen,o)                          # em FAS a ETE-ref e necessaria=False -> ja sai daqui
        pend=[r for r in reqs if ready[r.id] is None]
        if pend:
            r=pend[0]; elig[o.id]=False
            motivo[o.id]=f"SEM RECEITA: {r.tipo} '{r.id}' ({r.responsavel}) nao feito (caminho incompleto)."
            continue
        elig[o.id]=True; chain_last[o.id]=max((ready[r.id] for r in reqs),default=0)
    # ---- TRAVA DE CAPACIDADE: modulos sao obras que liberam vazao ao longo do tempo ----
    if FAS:
        for sis,e in cen.ete_do_sistema.items():
            mods=getattr(cen,"modulos_sis",{}).get(sis,[])
            rtp=sorted((ready[m.id],m.id) for m in mods if ready.get(m.id) is not None)
            nb=len(rtp)
            cs=sorted((o for o in cen.coletas if elig.get(o.id) and cen.nos[o.no].sistema==sis),
                      key=lambda o: chain_last[o.id])
            cum=0.0
            for o in cs:
                cum+=cen.vazao.get(o.no,0.0)
                need=int(math.ceil(max(0.0,cum-e.folga)/e.cap_modulo)) if e.cap_modulo>0 else (1 if cum>e.folga else 0)
                if need>nb:                              # nao ha modulos construidos suficientes -> nao fatura
                    elig[o.id]=False
                    motivo[o.id]="SEM RECEITA: capacidade da ETE insuficiente (faltam modulos construidos)."
                    continue
                need_o[o.id]=need
                if need>0: chain_last[o.id]=max(chain_last[o.id],rtp[need-1][0])
    # ---- inicio de faturamento (calculado ANTES da receita, p/ montar a cobertura REALIZADA) ----
    for o in cen.coletas:
        if not elig.get(o.id): continue
        inicio[o.id]=((chain_last[o.id]//12)+1)*12+o.lag   # JAN do ano seguinte a ficar tudo pronto, + lag
    # ---- FATOR esgoto/agua ENDOGENO (cobertura realizada do plano -> faixa) ----
    _fatc=_fator_por_cobertura_realizada(cen,elig,inicio,anos)
    # ---- receita das ligacoes NOVAS (com o fator do ano) ----
    for o in cen.coletas:
        if not elig.get(o.id): continue
        vpl+=_pv_receita(cen,o,inicio[o.id],_fatc.get(cen.cidade_da(o)))
    # ---- EFEITO-BASE: reajuste da equivalencia sobre as ligacoes JA EXISTENTES ----
    _vbase=_pv_efeito_base(cen,_fatc); vpl+=_vbase
    receita_ano=[0.0]*anos                                # receita nominal por ano (p/ graficos/fluxo de caixa)
    for o in cen.coletas:
        if not elig.get(o.id): continue
        ini2=inicio[o.id]; Hm2=cen.horizonte(o)*12
        rdm=o.receita_dir_regime()/12.0; ri=o.receita_ind_total()
        _fe=_fatc.get(cen.cidade_da(o)) or [1.0]*anos
        for m in range(ini2,Hm2):
            k=m-ini2; _Y=m//12; _f=(_fe[_Y] if _Y<len(_fe) else _fe[-1])
            r=rdm*_rampa(k,o.mat)*_f+ri*max(0.0,_rampa(k,o.mat)-_rampa(k-1,o.mat))
            if r and m//12<anos: receita_ano[m//12]+=r
    # ---- OPEX caminha com a RECEITA (obra ociosa nao gera OPEX) ----
    opex_ini={}
    for c in cen.coletas:
        if not elig.get(c.id): continue
        f=inicio[c.id]
        for r in requisitos(cen,c):
            if r.id not in opex_ini or f<opex_ini[r.id]: opex_ini[r.id]=f
    if FAS:                                              # OPEX de cada modulo comeca com a receita que ele libera
        for sis,e in cen.ete_do_sistema.items():
            mods=getattr(cen,"modulos_sis",{}).get(sis,[])
            rtp=sorted((ready[m.id],m.id) for m in mods if ready.get(m.id) is not None)
            for o in cen.coletas:
                if not elig.get(o.id) or cen.nos[o.no].sistema!=sis: continue
                for j in range(need_o.get(o.id,0)):
                    mid=rtp[j][1]; f=inicio[o.id]
                    if mid not in opex_ini or f<opex_ini[mid]: opex_ini[mid]=f
    for oid,o in cen.obras.items():
        if o.opex_ano<=0: continue
        st=opex_ini.get(oid)
        if st is None: continue
        tx=cen.taxa_de(o);Hm=cen.horizonte(o)*12;opm=o.opex_ano/12.0
        for m in range(st,Hm):
            _mp=_opex_mult(m-st, o.mat)                    # perfil: sobe ate o max no rampup (maturacao) da obra
            vpl-=opm*_mp/(1.0+tx)**(m//12)
            if m//12<anos: opex_ano[m//12]+=opm*_mp
    # ---- COBERTURA por sistema (ligacoes tratadas por ano) + deficit vs metas (SOFT) ----
    metas=getattr(cen,"metas_cobertura",{}); peso=getattr(cen,"peso_cobertura",0.0)
    maxl=getattr(cen,"max_lig",{}); basel=getattr(cen,"base_lig",{})
    cob_sis={}; deficit=0.0; metas_det=[]; metas_nao=0
    if metas:
        for cid in set(n.cidade for n in cen.nos.values()): cob_sis[cid]=[basel.get(cid,0.0)]*anos
        for o in cen.coletas:
            if not elig.get(o.id): continue
            sis=cen.nos[o.no].cidade; yop=chain_last.get(o.id,0)//12; arr=cob_sis[sis]
            for Y in range(anos):
                if Y>=yop: arr[Y]+=o.lig_cob*_uf(cen,o.no)
        _ab=getattr(cen,"ano_base",{})
        for sis,alvos in metas.items():
            _base=_ab.get(sis, min(_ab.values()) if _ab else 0)
            for ano,pct in alvos.items():
                idx=int(ano)-int(_base)                       # ano CALENDARIO (2030) -> indice interno (2030-ano_base)
                if idx<0 or idx>=anos or idx>=int(getattr(cen,"anos_capex",anos)): continue  # fora do horizonte de CAPEX -> ignora meta
                alvo=pct*maxl.get(sis,0.0); cobv=cob_sis.get(sis,[0.0]*anos)[idx]
                d=max(0.0,alvo-cobv); deficit+=d
                nao=1 if d>1e-6 else 0; metas_nao+=nao
                metas_det.append({"sistema":sis,"ano":int(ano),"pct":pct,"alvo":alvo,"cobertura":cobv,"deficit":d,"atingida":(nao==0)})
    _modo=getattr(cen,"penalidade_cobertura","meta+cobertura")
    if _modo=="ligacao":                                      # so maximiza cobertura TOTAL
        _penal=deficit
    elif _modo=="meta":                                       # so cumprir metas (sobra vai p/ VPL)
        _penal=metas_nao
    else:                                                     # META+COBERTURA: metas 1o; sobra -> max cobertura total
        _tot=max(1.0,getattr(cen,"total_max_lig",sum(maxl.values())))
        _cobdef=sum(max(0.0,maxl.get(_s,0.0)-cob_sis[_s][-1]) for _s in cob_sis)
        _penal=metas_nao + _cobdef/_tot                       # fracao<1 nunca atropela uma meta
    vpl_obj=vpl-peso*_penal
    cob={}
    for cid,c in cen.cidades.items():
        # `lig_cob`, e nao `lig`: este bloco compara com `meta_aumento`, que esta na moeda
        # da COBERTURA. Somar ligacoes totais contra uma meta residencial daria a meta por
        # cumprida com ligacao que ela nao conta.
        add=sum(o.lig_cob for o in cen.coletas if elig.get(o.id,False) and cen.cidade_da(o)==cid)
        nao=sum(o.lig_cob for o in cen.coletas if o.necessaria and plano.get(o.id) is not None
                and cen.cidade_da(o)==cid and not elig.get(o.id,False))
        cob[cid]={"adicionado":add,"nao_tratado":nao,"cob_final":c.cob_atual+add,"meta":c.meta_aumento,
                  "pct":(add/c.meta_aumento if c.meta_aumento>0 else 1.0),"ok":add>=c.meta_aumento-1e-9}
    # ---- EBITDA (saida calculada, NAO entra na funcao objetivo) ----
    # EBITDA de curto prazo = receita operacional - OPEX, ano a ano, em valores NOMINAIS.
    # Receita operacional = receita das ligacoes novas + efeito-base da paridade (reajuste na base).
    efeito_base_ano=_efeito_base_por_ano(cen,_fatc,anos)
    ebitda_ano=[receita_ano[_y]+efeito_base_ano[_y]-opex_ano[_y] for _y in range(anos)]
    return {"vpl":vpl,"vpl_obj":vpl_obj,"capex_ano":capex_ano,"opex_ano":opex_ano,"receita_ano":receita_ano,
            "efeito_base_ano":efeito_base_ano,"ebitda_ano":ebitda_ano,"cobertura":cob,
            "cobertura_sistema":cob_sis,"deficit_cobertura":deficit,"metas_nao_atingidas":metas_nao,"metas_detalhe":metas_det,
            "elig":elig,"motivo":motivo,"inicio_fat":inicio,"opex_ini":opex_ini,"ready":ready,"plano":dict(plano),
            "fator_esgoto_ano":_fatc,"vp_efeito_base":_vbase}

def vpl_por_subbacia(cen,res):
    """Decompoe o VPL do plano por SUB-BACIA, com RATEIO POR VAZAO das obras COMPARTILHADAS
    (transporte a jusante e modulos de ETE): cada sub-bacia paga a fatia do custo proporcional ao
    esgoto que manda por aquela obra — as MESMAS fracoes ja usadas na ponderacao do WACC.
    Como as fracoes somam 1, a SOMA dos VPLs por sub-bacia reproduz EXATAMENTE o VPL do plano.
    Receita direta, indireta e efeito-base ja sao proprios de cada sub-bacia (nao ha rateio).
    Retorna {sub-bacia: {capex, opex, rec_dir, rec_ind, efeito_base, vpl}}."""
    plano=res.get("plano",{}) or {}; elig=res.get("elig",{}) or {}
    opini=res.get("opex_ini",{}) or {}; inif=res.get("inicio_fat",{}) or {}
    fatc=res.get("fator_esgoto_ano",{}) or {}
    # quem EXIGE cada obra (topologico) — mesma logica de _vazao_por_obra
    by_rede={}; by_transp={}
    for q in cen.obras.values():
        if q.tipo=="rede": by_rede.setdefault(q.no,[]).append(q.id)
        elif q.tipo=="transporte": by_transp.setdefault(q.no,[]).append(q.id)
    req_sb={}
    for c in cen.coletas:
        X=c.no; ids=[c.id]+by_rede.get(X,[])
        for n in caminho(cen,X): ids+=by_transp.get(n,[])
        sis=cen.nos[X].sistema
        if sis in cen.ete_do_sistema: ids.append(cen.ete_do_sistema[sis].id)
        for rid in ids: req_sb.setdefault(rid,[]).append(X)
    sys_sub={}
    for sb,no in cen.nos.items(): sys_sub.setdefault(no.sistema,[]).append(sb)
    out={sb:{"capex":0.0,"opex":0.0,"rec_dir":0.0,"rec_ind":0.0,"efeito_base":0.0,"vpl":0.0} for sb in cen.nos}
    # ---------- CUSTOS: CAPEX e OPEX rateados por vazao ----------
    for oid,o in cen.obras.items():
        if not o.necessaria: continue
        y=plano.get(oid)
        if o.eh_aegea() and y is None: continue
        vc=_pv_custo(cen,o,y if o.eh_aegea() else 0)
        vo=0.0; st=opini.get(oid)
        if o.opex_ano>0 and st is not None:
            tx=cen.taxa_de(o); Hm=cen.horizonte(o)*12
            vo=-sum((o.opex_ano/12.0)*_opex_mult(m-st, o.mat)/(1.0+tx)**(m//12) for m in range(st,Hm))
        if abs(vc)<1e-12 and abs(vo)<1e-12: continue
        if o.tipo=="ete_mod": subs=list(sys_sub.get(getattr(o,"sistema",None),[]))     # modulo: todo o sistema
        else:
            subs=list(req_sb.get(oid,[]))
            if not subs:                                                               # fallback: a propria sub-bacia
                _n=getattr(o,"no",None)
                subs=[_n] if _n in out else list(sys_sub.get(getattr(o,"sistema",None),[]))
        subs=[s for s in subs if s in out]
        if not subs: continue
        tot=sum(cen.vazao.get(s,0.0) for s in subs)
        for s in subs:
            fr=(cen.vazao.get(s,0.0)/tot) if tot>0 else (1.0/len(subs))
            out[s]["capex"]+=vc*fr; out[s]["opex"]+=vo*fr
    # ---------- RECEITAS e EFEITO-BASE: proprios da sub-bacia ----------
    f0={}
    for cid,fx in (getattr(cen,"fator_esgoto",{}) or {}).items():
        fx=sorted(fx); mx=float((getattr(cen,"max_lig",{}) or {}).get(cid,0.0) or 0.0)
        bs=float((getattr(cen,"base_lig",{}) or {}).get(cid,0.0) or 0.0)
        f0[cid]=_faixa_fator(fx,(bs/mx) if mx>0 else 0.0)
    sr=getattr(cen,"sub_receita",{}) or {}
    for o in cen.coletas:
        sb=o.no
        if sb not in out: continue
        cid=cen.cidade_da(o); fe=fatc.get(cid); tx=_wacc_receita(cen,o); Hm=cen.horizonte(o)*12
        if elig.get(o.id) and inif.get(o.id) is not None:
            ini=inif[o.id]; rdm=o.receita_dir_regime()/12.0; ri=o.receita_ind_total()
            nf=len(fe) if fe else 0
            for m in range(ini,Hm):
                k=m-ini; Y=m//12; f=(fe[Y] if (fe and Y<nf) else (fe[-1] if fe else 1.0))
                out[sb]["rec_dir"]+=rdm*_rampa(k,o.mat)*f/(1.0+tx)**Y
                out[sb]["rec_ind"]+=ri*max(0.0,_rampa(k,o.mat)-_rampa(k-1,o.mat))/(1.0+tx)**Y
        d=sr.get(sb)
        if d and fe:
            base_ano=float(d.get("atuais",0.0) or 0.0)*float(d.get("ticket",0.0) or 0.0)*12.0*float(d.get("arrec",1.0) or 1.0)
            fr0=f0.get(cid,1.0)
            for Y in range(min(len(fe),cen.horizonte(o))):
                out[sb]["efeito_base"]+=base_ano*(fe[Y]-fr0)/(1.0+tx)**Y
    for sb,d in out.items(): d["vpl"]=d["capex"]+d["opex"]+d["rec_dir"]+d["rec_ind"]+d["efeito_base"]
    return out

def auditar_orcamento(cen,res):
    """Confere o CAPEX realizado do plano contra o teto de cada ano.
    Retorna (ok, violacoes) com violacoes = [(ano_calendario, indice, gasto, teto, excesso)]."""
    reg=list(cen.regionais)[0]
    ab=min(cen.ano_base.values()) if getattr(cen,"ano_base",None) else 2026
    gasto=res["capex_ano"][reg]; teto=cen.orc[reg]
    ac=int(getattr(cen,"anos_capex",len(teto)))
    v=[]
    for y in range(min(len(gasto),len(teto),ac)):      # teto ANUAL: so na janela de CAPEX
        if gasto[y] > teto[y]+1.0:
            v.append((ab+y, y, gasto[y], teto[y], gasto[y]-teto[y]))
    _tj=cen.orc_total if getattr(cen,"orc_total",None) else (getattr(cen,"orc_janela_total",{}) or {}).get(reg, sum(teto[:ac]))
    if sum(gasto) > _tj+1.0:                            # teto TOTAL da janela (custeia o rabo pos-janela)
        v.append((ab+ac, -1, sum(gasto), _tj, sum(gasto)-_tj))
    return (not v), v


def meses_permitidos(cen,o):
    """Meses internos (0-based) em que a obra pode INICIAR.
       Janela de INICIO limitada por cen.anos_capex (horizonte de otimizacao de CAPEX);
       o VPL continua ate cen.anos (fim de concessao). obra proibida=piso; obrigatoria=ano/janela."""
    ac=int(getattr(cen,"anos_capex",cen.anos)); Hm=ac*12
    _ex=int(getattr(cen,"anos_extra",0)); Hlim=(ac+_ex)*12      # obra INICIADA na janela pode CONCLUIR ate janela + anos_extra
    pe=int(getattr(o,"prazo",0) or 0)
    hi=min(Hm, Hlim-pe+1) if pe>0 else Hm                       # INICIO dentro da janela; conclusao ate janela + extra
    base=[m for m in range(max(0,hi)) if m>=o.inicio_min]
    _py=getattr(o,"_obrig_planyear",None)
    if _py is not None:                                  # ano-plano EXATO (janela de 12 meses)
        lo=(_py-1)*12; base=[m for m in base if lo<=m<lo+12]
    return base

def viavel(cen,plano):
    _dimensiona_etes(cen,plano)
    for e in cen.ete_do_sistema.values():
        if getattr(e,"nova",False) and e.cap_max is not None and e.demanda>e.cap_max+1e-9:
            return False,f"ETE nova {e.id}: vazao conectada {e.demanda:.0f} > capacidade {e.cap_max:.0f}"
    anos=cen.anos;capex={reg:[0.0]*anos for reg in cen.regionais}
    for oid,y in plano.items():
        o=cen.obras[oid]
        if y is None:
            if o.obrigatoria and o.eh_aegea(): return False,f"obrigatoria {oid} nao feita"
            continue
        if not o.eh_aegea(): continue
        if y not in meses_permitidos(cen,o): return False,f"{oid} mes {y} (ano {y//12+1}) nao permitido"
        pe=o.prazo;reg=cen.regional_da(o)
        if pe>0:
            cm=o.capex/pe
            for m in range(y,y+pe):
                if m//12<anos: capex[reg][m//12]+=cm
        elif y//12<anos: capex[reg][y//12]+=o.capex
    _ac=int(getattr(cen,"anos_capex",anos)); _jt=getattr(cen,"orc_janela_total",None) or {}
    for reg in cen.regionais:                          # teto ANUAL na janela + teto TOTAL da janela (custeia o rabo)
        for t in range(min(_ac,anos)):
            if capex[reg][t]>cen.orc[reg][t]+1e-6: return False,f"orcamento {reg} ano {t+1}"
        _teto=cen.orc_total if getattr(cen,"orc_total",None) else _jt.get(reg,sum(cen.orc[reg][:_ac]))
        if sum(capex[reg])>_teto+1e-6: return False,f"orcamento total da janela {reg}"
    return True,"ok"

# ---------- solvers

def imprimir(cen,res,titulo="RESULTADO"):
    print(f"\n=== {titulo} ===");print(f"VPL total: R$ {res['vpl']:,.0f}")
    def _ma(m): return f"mes {m} (ano {m//12+1})"
    print("Obras (responsavel | inicio -> pronta | capex por componente):")
    for oid,y in sorted(res["plano"].items()):
        o=cen.obras[oid];rd=res["ready"][oid]
        if not o.necessaria: q="nao necessaria"
        elif o.responsavel=="Terceiro": q=f"TERCEIRO -> pronta {_ma(rd)}"
        elif y is None: q="NAO feita"
        else: q=f"{_ma(y)} -> pronta {_ma(rd)}"
        comp=", ".join(f"{k}={v:,.0f}" for k,v in o.capex_comp.items() if v>0) or "cap 0"
        _u=capex_unitario_txt(o,curto=True)
        if _u: comp=f"{comp}  [{_u}]"
        rc=""
        if o.tipo=="coleta" and o.necessaria:
            rc=(f"FATURA {_ma(res['inicio_fat'][o.id])}" if res["elig"].get(o.id)
                else (res["motivo"].get(o.id,"") if (o.eh_aegea() and y is not None) else ""))
        alvo=o.no if o.tipo!="ete" else f"sistema {o.sistema}"
        print(f"  {oid:14} [{o.tipo}@{alvo} | {o.responsavel}] {q:26} {comp}  {('-> '+rc) if rc else ''}")
    print("Cobertura por cidade (so ligacoes tratadas):")
    for cid,d in res["cobertura"].items():
        flag="META OK" if d["ok"] else f"{d['pct']*100:.0f}% da meta"
        ex=f" | {d['nao_tratado']:.0f} lig sem tratamento" if d["nao_tratado"]>0 else ""
        print(f"  {cid}: +{d['adicionado']:.0f}/{d['meta']:.0f} lig -> {flag}{ex}")






def ler_banco(abas, orcamento=None, horizonte_capex=None, ete_fixo=False, ete_faseada=False, metas_cobertura=None, peso_cobertura=0.0, foco_cobertura=None, penalidade_cobertura="meta+cobertura", data_inicio=None, orcamento_total=None, peso_cidade=None, regional=None, unidade=None, curva_adocao="scurve", base_receita="arrecadada", anos_extra_conclusao=3, usar_cts=True, cobertura_so_residencial=False):
    """Monta o Cenario a partir das ABAS do input, no formato de JUNCOES: hierarquia em
    tabelas de ligacao, sistema-topologia (jusante), subbacia-operacional (=sub-bacia),
    componentes/ete-capex, regional-operacional. Cobertura e CONCESSAO por CIDADE
    (cidade-operacional + metas-cobertura por cidade). Horizonte do sistema = fim da sua
    cidade; taxa por REGIONAL.

    `abas` E UM DICIONARIO {nome_da_aba: [linha, ...]}, e cada linha e um dicionario de
    coluna -> valor, com a coluna ja normalizada (minuscula, `_` no lugar de espaco e
    hifen). Nao e um caminho de arquivo: o motor nao le planilha.

    Quem monta o dicionario e a FONTE — `carregar_postgres.abas_do_postgres` em producao,
    `tests/_helpers` a partir dos JSON de fixture. Assim a mesma funcao serve as duas sem
    que nenhuma delas precise materializar arquivo nenhum.
    """
    from collections import defaultdict
    if isinstance(abas, (str, bytes)) or hasattr(abas, "__fspath__"):
        raise TypeError(
            "ler_banco recebe as ABAS (dict), nao um caminho de arquivo. "
            "Use carregar_postgres.abas_do_postgres(pg_url) para ler do banco.")
    _BREC = "faturada" if str(base_receita).strip().lower().startswith("fat") else "arrecadada"  # base de receita da rodada
    def L(*nomes):
        """A aba pelo primeiro nome que existir — a lista de alternativas cobre a grafia
        com e sem acento, que difere entre as fontes."""
        for n in nomes:
            linhas = abas.get(n)
            if linhas is not None: return linhas
        return []
    def num(v,dv=0.0):
        try: return float(v)
        except: return dv
    def cal2py(v,ab):                                    # ano-calendario -> ano-plano 1-based; preserva 0 e -1
        try: v=int(round(float(v)))
        except: return 0
        if v==0 or v==-1: return v
        if v>=1900: return max(1, v-int(ab)+1)           # ano-calendario (2028) -> indice de ano-plano
        return v                                         # compat: ja e ano-plano/codigo antigo
    def sim(v): return str(v).strip().lower() in ("sim","s","true","1","x","aegea")
    # hierarquia
    reg_name={};uni_reg={};uni_name={};uni_wacc_medio={}
    for d in L("unidade-regional","unidade_regional"):
        reg_name[d["regional_id"]]=d.get("regional_name") or d["regional_id"];uni_reg[d["unidade_id"]]=d["regional_id"]
        uni_name[d["unidade_id"]]=d.get("unidade_name") or d["unidade_id"]
        _wmr=d.get("wacc_medio")                       # WACC MEDIO da unidade (Operacoes Financeiras) — fallback
        if _wmr is not None and str(_wmr).strip()!="": uni_wacc_medio[d["unidade_id"]]=num(_wmr)
    sup_uni={d["superintendencia_id"]:d["unidade_id"] for d in L("regional-superintendencia","regional-superintendência")}
    cid_sup={};cid_name={}
    for d in L("superintendencia-cidade"):
        cid_sup[d["cidade_id"]]=d["superintendencia_id"];cid_name[d["cidade_id"]]=d.get("cidade_name") or d["cidade_id"]
    sis_cid={};sis_name={}
    for d in L("cidade-sistema"):
        sis_cid[d["sistema_id"]]=d["cidade_id"];sis_name[d["sistema_id"]]=d.get("sistema_name") or d["sistema_id"]
    # ---- REGIONAL: o otimizador roda para UMA regional por vez. -----------------
    # 'regional' aceita o id (r1) ou o nome ("Regional Metropolitana"). Com o banco de uma
    # regional so, o parametro e opcional e o comportamento nao muda.
    # ---- ESCOPO DA ANALISE = UNIDADE ------------------------------------------------
    # Hierarquia: REGIONAL > UNIDADE > SUPERINTENDENCIA > CIDADE > SISTEMA > SUB-BACIA.
    # Cada UNIDADE e analisada de forma TOTALMENTE INDEPENDENTE — inclusive duas unidades da
    # mesma regional. Por isso 'unidade' e o parametro natural; 'regional' so serve de atalho
    # quando aquela regional tem uma unica unidade.
    if unidade is None and regional is not None:
        _r_alvo=None
        for _r in sorted(set(uni_reg.values())):
            if str(regional).strip()==str(_r) or str(regional).strip().lower()==str(reg_name.get(_r,_r)).strip().lower():
                _r_alvo=_r; break
        if _r_alvo is None:
            raise ValueError("regional '%s' nao existe no banco. Disponiveis: %s"
                             % (regional, [(r, reg_name.get(r,r)) for r in sorted(set(uni_reg.values()))]))
        _us=[u for u,r in uni_reg.items() if r==_r_alvo]
        if len(_us)>1:
            raise ValueError("a regional '%s' tem %d unidades e a analise e POR UNIDADE, uma de cada vez — "
                             "informe unidade=... . Disponiveis: %s"
                             % (reg_name.get(_r_alvo,_r_alvo), len(_us), [(u, uni_name.get(u,u)) for u in sorted(_us)]))
        unidade=_us[0] if _us else None
    if unidade is not None:
        _unis=sorted(uni_reg)
        _au=None
        for _u in _unis:
            if str(unidade).strip()==str(_u) or str(unidade).strip().lower()==str(uni_name.get(_u,_u)).strip().lower():
                _au=_u; break
        if _au is None:
            raise ValueError("unidade '%s' nao existe no banco. Disponiveis: %s"
                             % (unidade, [(u, uni_name.get(u,u)) for u in _unis]))
        _cid_ok={_c for _c,_sp in cid_sup.items() if sup_uni.get(_sp)==_au}
        _sis_ok={_s for _s,_c in sis_cid.items() if _c in _cid_ok}
        _ar=uni_reg[_au]
        reg_name={_k:_v for _k,_v in reg_name.items() if _k==_ar}
        uni_reg={_au:_ar}
        uni_name={_au:uni_name.get(_au,_au)}
        sup_uni={_k:_v for _k,_v in sup_uni.items() if _v==_au}
        cid_sup={_k:_v for _k,_v in cid_sup.items() if _k in _cid_ok}
        cid_name={_k:_v for _k,_v in cid_name.items() if _k in _cid_ok}
        sis_cid={_k:_v for _k,_v in sis_cid.items() if _k in _sis_ok}
        sis_name={_k:_v for _k,_v in sis_name.items() if _k in _sis_ok}
        print(f"  [info] ESCOPO = UNIDADE {uni_name.get(_au,_au)} (regional {reg_name.get(_ar,_ar)}) — "
              f"{len(_cid_ok)} cidades, {len(_sis_ok)} sistemas. Todos os dados a seguir sao dessa unidade, "
              f"que e analisada de forma independente das demais.")
        _regs=[_ar]; _alvo=_ar
    else:
      _regs=sorted(set(uni_reg.values()))
      if regional is not None:
        _alvo=None
        for _r in _regs:
            if str(regional).strip()==str(_r) or str(regional).strip().lower()==str(reg_name.get(_r,_r)).strip().lower():
                _alvo=_r; break
        if _alvo is None:
            raise ValueError("regional '%s' nao existe no banco. Disponiveis: %s"
                             % (regional, [(r, reg_name.get(r,r)) for r in _regs]))
      elif len(_regs)>1 or len(uni_reg)>1:
        raise ValueError("a analise e POR UNIDADE, uma de cada vez — informe unidade=... . Disponiveis: %s"
                         % [(u, uni_name.get(u,u), reg_name.get(r,r)) for u,r in sorted(uni_reg.items())])
      else:
        _alvo=_regs[0] if _regs else None
      if _alvo is not None:
        _cid_ok={_c for _c,_sp in cid_sup.items() if uni_reg.get(sup_uni.get(_sp))==_alvo}
        _sis_ok={_s for _s,_c in sis_cid.items() if _c in _cid_ok}
        reg_name={_k:_v for _k,_v in reg_name.items() if _k==_alvo}
        uni_reg={_k:_v for _k,_v in uni_reg.items() if _v==_alvo}
        uni_name={_k:_v for _k,_v in uni_name.items() if _k in uni_reg}
        sup_uni={_k:_v for _k,_v in sup_uni.items() if _v in uni_reg}
        cid_sup={_k:_v for _k,_v in cid_sup.items() if _k in _cid_ok}
        cid_name={_k:_v for _k,_v in cid_name.items() if _k in _cid_ok}
        sis_cid={_k:_v for _k,_v in sis_cid.items() if _k in _sis_ok}
        sis_name={_k:_v for _k,_v in sis_name.items() if _k in _sis_ok}
        if len(_regs)>1:
            print(f"  [info] REGIONAL selecionada: {reg_name.get(_alvo,_alvo)} "
                  f"({len(_cid_ok)} cidades, {len(_sis_ok)} sistemas) — as outras {len(_regs)-1} ficam de fora. "
                  f"Todos os dados a seguir sao dessa regional.")
    def reg_de_sis(sis): return uni_reg[sup_uni[cid_sup[sis_cid[sis]]]]
    def uni_de_sis(sis): return sup_uni[cid_sup[sis_cid[sis]]]          # UNIDADE = escopo de orcamento
    _wm_used={'n':0}
    def _wacc_fb(raw, sis):
        # WACC do componente = financiamento contratado; se VAZIO, consome o wacc_medio da UNIDADE
        if raw is not None and str(raw).strip()!="": return raw
        try: _u=uni_de_sis(sis)
        except Exception: _u=None
        wm=uni_wacc_medio.get(_u)
        if wm is not None: _wm_used['n']+=1
        return wm
    regop={d["regional_id"]:d for d in L("regional-operacional")}
    _tx_obs=[d.get("regional_id") for d in L("regional-operacional") if d.get("taxa_desconto") is not None]
    if _tx_obs:
        print(f"  [aviso] coluna 'taxa_desconto' em regional-operacional IGNORADA ({len(_tx_obs)} linha(s)): "
              f"o desconto e por elemento (coluna 'wacc'), nao por regional/unidade.")
    orc_reg={d["regional_id"]:num(d.get("valor_ano")) for d in L("orcamento")}
    cidop={d["cidade_id"]:d for d in L("cidade-operacional")}                         # CONCESSAO por CIDADE
    fim_cid={_c:int(num(_d.get("data_fim_concessao"),0)) for _c,_d in cidop.items()}
    _sisop={d["sistema_id"]:d for d in L("sistema-operacional")}                       # compat: fim por sistema (bancos antigos)
    fim_sis={_s:int(num(_d.get("data_fim_concessao"),0)) for _s,_d in _sisop.items()}
    # `dict(d)`, e nao `d`: ADIANTE ESTA FUNCAO ESCREVE nestas linhas — as colunas
    # `*_novas_obras` sao derivadas por cima do que veio, e no modo sem CTS as colunas
    # exclusivas recebem o valor consolidado. Sem a copia, quem chamou fica com o dicionario
    # ALTERADO, e o `abas_fonte` do snapshot publicaria o dado JA DERIVADO como se fosse o
    # input bruto — a auditoria passaria a mentir sobre a origem.
    #
    # Copia RASA e o bastante: os valores sao escalares, e o que nao pode ser compartilhado
    # e o dicionario da linha.
    subop={(d.get("sub_bacia") or d.get("subsistema_id")):dict(d) for d in L("subbacia-operacional","subsistema-operacional")}
    # ---- CTS (Coletor de Tempo Seco): estrutura irma da sub-bacia, pareada 1:1 pela aba subbacia-cts ----
    _cts_op={d.get("cts"): dict(d) for d in L("cts-operacional") if d.get("cts")}   # copia: ver `subop` acima
    _cts_dep={d.get("sub_bacia"): d.get("cts") for d in L("subbacia-cts") if d.get("sub_bacia") and d.get("cts")}
    _cts_ids_all=set(_cts_op)
    # ---- RECORTE DA COBERTURA: total x so residencial -----------------------------
    # `cobertura_so_residencial=True` mede a cobertura CONTANDO SO LIGACOES E ECONOMIAS
    # RESIDENCIAIS. O universo residencial e as residenciais atendidas vem do banco em
    # COLUNA PROPRIA (`*_residencial`), medidas — nao sao mais deduzidas subtraindo uma
    # parcela industrial nem estimadas por proporcao, como na versao anterior.
    #
    # O RECORTE ACABA NA COBERTURA. Receita, VPL, vazao, CAPEX e OPEX seguem no TOTAL, em
    # qualquer modo: quem paga a conta e a ligacao, seja ela de casa ou de fabrica. A meta
    # contratual e que e residencial. Sao moedas diferentes, e o motor passou a carregar as
    # duas (ver `Obra.lig` x `Obra.lig_cob`).
    #
    # POPULACAO nao tem versao residencial: industria nao mora, entao o universo de
    # populacao ja e residencial por natureza.
    _COB_LIG=("universo_ligacoes_residencial","ligacoes_atuais_residencial","ligacoes_novas_obras_residencial")
    _COB_ECO=("universo_economias_residencial","economias_atuais_residencial","economias_novas_obras_residencial")
    _cob_res=bool(cobertura_so_residencial)
    if _cob_res:
        # SEM AS COLUNAS, NAO HA RECORTE A FAZER — e cair no total EM SILENCIO seria o pior
        # desfecho: a rodada responderia "so residencial" medindo todo mundo, e ninguem
        # notaria. Entao o modo se desliga com aviso alto, e o `milp_status` nao muda porque
        # o plano e o mesmo de uma rodada sem recorte.
        # `_cts_op` so entra na conta quando a CTS ESTA na rodada. Sem ela, os dados dela
        # nao sao lidos para nada — nem para diagnostico. Contar linhas que nao participam
        # faria o recorte se dar por atendido com base em dado que ninguem vai usar.
        _linhas_em_jogo=list(subop.values())+(list(_cts_op.values()) if usar_cts else [])
        _tem_res=sum(1 for _d in _linhas_em_jogo
                     if _d.get("universo_ligacoes_residencial") not in (None,""))
        if not _tem_res:
            print("  [ALERTA] COBERTURA SO RESIDENCIAL pedida, mas o banco nao tem "
                  "`universo_ligacoes_residencial` em nenhuma sub-bacia. A cobertura foi medida "
                  "no TOTAL — o recorte NAO foi aplicado.")
            _cob_res=False
        else:
            _falta=[_k for _k,_d in (list(subop.items())+(list(_cts_op.items()) if usar_cts else []))
                    if _d.get("universo_ligacoes_residencial") in (None,"")]
            if _falta:
                print(f"  [aviso] {len(_falta)} sub-bacia(s)/CTS sem coluna residencial: a cobertura delas "
                      f"cai para o TOTAL. Ex.: {_falta[:3]}")
            print(f"  [info] COBERTURA = SO RESIDENCIAL ({_tem_res} sub-bacia(s)/CTS com dado residencial). "
                  "Receita, VPL, vazao e CAPEX seguem no total.")
    def _usa_res(_sbk):
        """Esta sub-bacia mede a cobertura pelas colunas residenciais?

        Tres condicoes, e a terceira e a que custou um teste: CIDADE QUE MEDE EM
        POPULACAO FICA DE FORA. Industria nao mora, entao o universo de populacao ja e
        residencial — trocar o universo de ligacoes por residencial e depois multiplicar
        pela densidade populacional (habitantes por ligacao TOTAL) devolveria uma
        populacao menor que a real, que nao e a populacao de ninguem.

        `sis_de_sb` so existe mais adiante nesta funcao; como isto aqui e chamado depois,
        o nome resolve na CHAMADA e nao na definicao.
        """
        if not _cob_res: return False
        if (subop.get(_sbk) or {}).get(_COB_LIG[0]) in (None,""): return False
        _u=str((cidop.get(sis_cid.get(sis_de_sb.get(_sbk))) or {}).get("unidade_cobertura") or "ligacoes")
        return not _u.strip().lower().startswith("pop")
    # ---- CTS DESLIGADA: o que a sub-bacia absorve ---------------------------------
    # As colunas de LIGACAO e ECONOMIA da sub-bacia sao o que pertence EXCLUSIVAMENTE a
    # ela. A CTS cobre uma area que se SOBREPOE a essa — e a sobreposicao e contada uma
    # vez so, na entidade que a atende em cada cenario:
    #
    #   usar_cts=True   a CTS atende a sobreposicao; ela esta nos numeros da CTS, que
    #                   entra como no proprio. A sub-bacia usa as colunas exclusivas.
    #   usar_cts=False  o coletor nao existe; quem atende a sobreposicao e a sub-bacia,
    #                   e o total dela vem das colunas `*_com_cts` — exclusiva + sobreposta.
    #
    # POR QUE NAO SOMAR AS DUAS LINHAS, que era o que se fazia: a ligacao da area
    # sobreposta esta nas duas, entao a soma a CONTA DUAS VEZES. O universo da meta
    # crescia sozinho ao desligar a CTS, e a cobertura piorava sem nenhuma obra ter
    # mudado. As colunas `_com_cts` vem da origem ja consolidadas e nao tem esse defeito.
    _CTS_CONSOLIDADO=[("universo_ligacoes","universo_ligacoes_com_cts"),
                      ("ligacoes_atuais","ligacoes_atuais_com_cts"),
                      ("universo_economias","universo_economias_com_cts"),
                      ("economias_atuais","economias_atuais_com_cts"),
                      (_COB_LIG[0],"universo_ligacoes_residencial_com_cts"),
                      (_COB_LIG[1],"ligacoes_atuais_residencial_com_cts"),
                      (_COB_ECO[0],"universo_economias_residencial_com_cts"),
                      (_COB_ECO[1],"economias_atuais_residencial_com_cts")]
    # NADA MAIS E SOMADO. Vazao, receita e populacao sao DADO da sub-bacia, e o motor nao
    # inventa o valor delas para o cenario sem coletor: se desligar a CTS muda a vazao,
    # quem atualiza a base e quem cadastra. A escolha de considerar ou nao a CTS nao mexe
    # em receita.
    #
    # Era isto que a versao anterior fazia, e o defeito e visivel agora que as ligacoes
    # deixaram de ser somadas: a linha da CTS entrava inteira em vazao/receita/populacao,
    # enquanto as ligacoes vinham da coluna consolidada — duas moedas diferentes na mesma
    # sub-bacia. Somar tambem contava a area sobreposta duas vezes.
    #
    # O QUE ISSO CUSTA, DECLARADO: sem o coletor, a vazao que a sub-bacia manda para a ETE
    # e a que estiver na base dela. Se a base nao tiver sido atualizada para esse cenario,
    # a ETE e dimensionada sem o esgoto que vinha pelo coletor.
    _absorvidas=[]; _sem_consolidado=[]        # so o modo desligado preenche
    if usar_cts:                                   # LIGADO: CTS entra como no proprio (dados operacionais)
        for _c,_row in _cts_op.items(): subop[_c]=_row
    else:                                          # DESLIGADO: a sub-bacia le as colunas consolidadas
        # A UNICA DIFERENCA PARA A SUB-BACIA E QUAL COLUNA E LIDA. Nada e somado, nada e
        # ponderado, nada e derivado: as oito colunas `*_com_cts` substituem as exclusivas
        # e o resto da ficha — vazao, receita, populacao, potencial — fica como esta.
        #
        # Vazao, receita e populacao sao DADO da sub-bacia. Se desligar o coletor muda a
        # vazao dela, quem atualiza a base e quem cadastra; o motor nao arbitra o numero.
        # E a escolha nao mexe em receita.
        # As duas listas sao do BANCO INTEIRO — `subop` so e filtrado pela unidade mais
        # adiante. Elas viram aviso la embaixo, ja recortadas por `cen.nos`: dizer "337
        # sub-bacias" numa unidade que tem 186 (ou nenhuma) e ruido que ensina a ignorar
        # aviso. A linha `[info] CTS:` ao lado sempre fez esse recorte certo.
        for _sb,_c in _cts_dep.items():
            if _sb not in subop or _c not in _cts_op: continue
            _s=subop[_sb]
            if _s.get(_CTS_CONSOLIDADO[0][1]) in (None,""):
                # SEM A COLUNA nao ha o que ler, e o motor NAO inventa: fica a exclusiva.
                # A versao anterior somava a linha da CTS aqui, e era isso que contava a
                # area sobreposta duas vezes. Base que ainda nao tem a coluna produz uma
                # rodada sem CTS que ignora a demanda do coletor — e o aviso diz isso.
                _sem_consolidado.append(_sb)
                continue
            for _exc,_tot in _CTS_CONSOLIDADO:
                if _s.get(_tot) not in (None,""): _s[_exc]=num(_s.get(_tot))
        _absorvidas=[_sb for _sb,_c in _cts_dep.items() if _sb in subop and _c in _cts_op]
    # ---- UNIDADES ----------------------------------------------------------------
    # LIGACOES e ECONOMIAS vem SEMPRE da base comercial (Databricks) para toda sub-bacia.
    # POPULACAO e opcional e entra por input do usuario, quando precisa.
    # Por isso a DENSIDADE nao e mais um dado de entrada: ela e DERIVADA da propria base
    # (economias por ligacao = universo_economias / universo_ligacoes). Uma coluna a menos
    # para preencher e uma inconsistencia a menos para acontecer.
    # PRECOS (ticket medio e taxa de ligacao) sao SEMPRE POR LIGACAO — nao ha conversao.
    # A densidade entra apenas na COBERTURA, quando a cidade mede em economias ou populacao.
    def _densidade(_d, alvo):
        """<alvo> por ligacao, derivado da base. 0.0 se nao houver como derivar.

        `economias_res` e a densidade RESIDENCIAL — economias residenciais por ligacao
        residencial. Ela existe porque a conversao tem de ficar na mesma moeda da
        cobertura: converter ligacao residencial com densidade TOTAL misturaria as duas
        e daria uma cobertura que nao e nem uma coisa nem outra.
        """
        pares = (("universo_economias","universo_ligacoes"),
                 ("economias_atuais","ligacoes_atuais"),
                 ("economias_novas_obras","ligacoes_novas_obras")) if alvo=="economias" else \
                (tuple(zip(_COB_ECO,_COB_LIG)) if alvo=="economias_res" else
                (("universo_populacao","universo_ligacoes"),
                 ("populacao_atual","ligacoes_atuais"),
                 ("populacao_novas_obras","ligacoes_novas_obras")))
        for _a,_b in pares:
            _va,_vb=_d.get(_a),_d.get(_b)
            if _va is not None and _vb is not None and num(_vb)>0 and num(_va)>0:
                return num(_va)/num(_vb)
        return 0.0
    _dv_ec=_dv_pp=0; _dens_sb={}; _div_dens=[]; _uni_obs=[]
    for _sbk,_d in subop.items():
        _dens=_densidade(_d,"economias"); _dpop=_densidade(_d,"populacao")
        # A residencial cai para a total quando nao ha dado residencial — e a mesma
        # degradacao por sub-bacia que o aviso do recorte anuncia.
        _dres=_densidade(_d,"economias_res") or _dens
        _dens_sb[_sbk]={"economias":_dens,"populacao":_dpop,"economias_res":_dres}
        # coluna antiga de densidade, se ainda existir, vira CONFERENCIA (nao entra no calculo)
        for _colv,_der,_rot in ((_d.get("densidade_economias"),_dens,"economias"),
                                (_d.get("densidade_populacao", _d.get("habitantes_por_ligacao")),_dpop,"populacao")):
            if _colv is not None and num(_colv)>0 and _der>0 and abs(num(_colv)-_der)/_der>0.02:
                _div_dens.append((_sbk,_rot,num(_colv),_der))
        # ligacoes DEVEM vir da base; se faltarem, reconstroi (defensivo) e avisa
        if _d.get("universo_ligacoes") is None:
            if _dens>0:
                for _lg,_ec in (("universo_ligacoes","universo_economias"),("ligacoes_atuais","economias_atuais"),("ligacoes_novas_obras","economias_novas_obras")):
                    if _d.get(_lg) is None and _d.get(_ec) is not None: _d[_lg]=num(_d.get(_ec))/_dens; _dv_ec+=1
            elif _dpop>0:
                for _lg,_pp in (("universo_ligacoes","universo_populacao"),("ligacoes_atuais","populacao_atual"),("ligacoes_novas_obras","populacao_novas_obras")):
                    if _d.get(_lg) is None and _d.get(_pp) is not None: _d[_lg]=num(_d.get(_pp))/_dpop; _dv_pp+=1
        # --- TICKET MEDIO e TAXA DE LIGACAO sao SEMPRE POR LIGACAO. ------------------
        # Vem assim da base comercial e assim entram na receita: nao ha conversao de preco.
        # A densidade so atua do lado da COBERTURA, quando a cidade mede em economias ou populacao.
        for _cu in ("unidade_ticket","unidade_preco_ligacao"):
            _v=_d.get(_cu)
            if _v is not None and str(_v).strip() and not str(_v).strip().lower().startswith("liga"):
                _uni_obs.append((_sbk,_cu,str(_v).strip()))
    if _dv_ec or _dv_pp:
        print(f"  [aviso] {_dv_ec+_dv_pp} campo(s) de LIGACOES ausentes na base e reconstruidos por conversao "
              f"(economias: {_dv_ec}, populacao: {_dv_pp}). Pela regra atual, ligacoes vem sempre da base comercial.")
    if _div_dens:
        print(f"  [aviso] {len(_div_dens)} coluna(s) 'densidade_*' do banco divergem da densidade DERIVADA (>2%) — "
              f"prevalece a derivada. Ex.: {[(a,b,f'{c:.2f} vs {d2:.2f}') for a,b,c,d2 in _div_dens[:3]]}")
    _nd=sum(1 for v in _dens_sb.values() if v['economias']>0)
    if _nd: print(f"  [info] densidade DERIVADA da base: {_nd} sub-bacia(s) com economias/ligacao")
    if _uni_obs:
        print(f"  [aviso] {len(_uni_obs)} celula(s) de 'unidade_ticket'/'unidade_preco_ligacao' com valor diferente de "
              f"'ligacao' — IGNORADAS: preco e sempre por ligacao. Ex.: {_uni_obs[:3]}")
    # ---- *_novas_obras e DERIVADO (nao e input do usuario): novas = max(0, universo - atuais) ----
    # Regra nova: ligacoes/economias/populacao "novas das obras" = universo - atuais (piso 0).
    # Cobre sub-bacia e CTS (ja mescladas no subop). Se o banco trouxer a coluna, vira CONFERENCIA
    # (prevalece o derivado) e a engine avisa quando divergir.
    _nov_div=[]
    for _sbk,_d in subop.items():
        for _un,_at,_nv in (("universo_ligacoes","ligacoes_atuais","ligacoes_novas_obras"),
                            ("universo_economias","economias_atuais","economias_novas_obras"),
                            ("universo_populacao","populacao_atual","populacao_novas_obras"),
                            # As residenciais derivam pela MESMA regra (universo - atuais), e nao
                            # por proporcao do total: o dado agora e medido dos dois lados, e
                            # derivar diferente faria a cobertura residencial ter uma aritmetica
                            # propria — que e como a versao anterior errava.
                            _COB_LIG, _COB_ECO):
            if _d.get(_un) in (None,"") or _d.get(_at) in (None,""): continue
            _der=max(0.0, num(_d.get(_un))-num(_d.get(_at)))
            _antigo=_d.get(_nv)
            if _antigo is not None and str(_antigo).strip()!="" and abs(num(_antigo)-_der)>1.0:
                _nov_div.append((_sbk,_nv,num(_antigo),_der))
            _d[_nv]=_der
    if _nov_div:
        print(f"  [aviso] {len(_nov_div)} valor(es) de '*_novas_obras' do banco divergem do DERIVADO "
              f"(universo - atuais) e foram SUBSTITUIDOS (ex.: {[(a,b,f'{c:.0f}->{d2:.0f}') for a,b,c,d2 in _nov_div[:3]]})")
    etecap={d["ete_id"]:d for d in L("ete-capex")}
    ete_ids=set(etecap)
    ete_do_sis={};sb_rows=[]
    for d in L("sistema-topologia"):
        if d.get("sistema_id") not in sis_cid: continue      # so a REGIONAL selecionada
        comp=d["componente_sistema_id"]
        if (not usar_cts) and comp in _cts_ids_all: continue   # CTS desligada: nao vira no
        if comp in ete_ids: ete_do_sis[d["sistema_id"]]=comp
        else: sb_rows.append(d)
    nos=[];sis_de_sb={}
    for d in sb_rows:
        sb=d["componente_sistema_id"];sis=d["sistema_id"];sis_de_sb[sb]=sis
        # O 4o campo do No e o EIXO DE ORCAMENTO — que agora e a UNIDADE, nao a regional.
        nos.append(No(sb,cid_name[sis_cid[sis]],sis_name[sis],uni_name[uni_de_sis(sis)],d.get("componente_sistema_id_jusante")))
    cidades=[Cidade(nm,0.0,0.0,0.0,99) for c,nm in cid_name.items()]   # cobertura e por SISTEMA (aba metas-cobertura); cidade so p/ rotulo
    hz={}; _anobase={}
    for sis in sis_cid:
        reg=reg_de_sis(sis);ab=int(num((regop.get(reg) or {}).get("ano_base"),2026))
        _fim=fim_cid.get(sis_cid[sis]) or fim_sis.get(sis) or (ab+20)                   # concessao pela CIDADE (fallback sistema/padrao)
        hz[sis_name[sis]]=max(1,int(_fim)-ab+1)                                         # fim INCLUSIVE; horizonte do sistema = fim da sua cidade
        _anobase[cid_name[sis_cid[sis]]]=ab                                             # ano_base por CIDADE (metas sao por cidade)
    # ORCAMENTO = entrada do codigo (recomendado): escalar (aplica a todas as regionais),
    # dict {regional_name: valor_ano | [por ano]}, ou None. Se None, tenta a aba 'orcamento'; senao infinito.
    INF=float("inf")
    _anos_h=max(hz.values()) if hz else 20
    _orc_cal=isinstance(orcamento,dict) and len(orcamento)>0 and all(isinstance(k,int) and 1900<=k<=2200 for k in orcamento)
    orc={}
    _ab_orc=min(_anobase.values()) if _anobase else 2026     # ano-base do cronograma = 1o ano-base das cidades
    for u,r in uni_reg.items():                              # ORCAMENTO E POR UNIDADE
        un=uni_name[u]; ab=_ab_orc
        if _orc_cal:                   # {ano_calendario: teto}: verba por ano (anos sem verba = 0)
            orc[un]=[float(orcamento.get(ab+Y,0.0)) for Y in range(_anos_h)]
        elif isinstance(orcamento,dict): orc[un]=orcamento.get(un, orcamento.get(reg_name.get(r,r), INF))
        elif orcamento is not None:      orc[un]=float(orcamento)
        elif u in orc_reg:               orc[un]=orc_reg[u]
        elif r in orc_reg:               orc[un]=orc_reg[r]
        else:                            orc[un]=INF
    if orcamento is None and not orc_reg:
        print("  [aviso] orcamento nao informado (parametro nem aba) -> CAPEX sem teto")
    comp=defaultdict(list)
    for d in L("componentes-subbacias-capex","componentes-subacias-capex"): comp[d.get("sub_bacia")].append(d)
    if usar_cts:
        for d in L("componentes-cts-capex"):
            if d.get("cts"): comp[d.get("cts")].append(d)
    def _codigo(nome):
        n=nome.lower()
        if "tempo seco" in n: return "cts","coleta"    # Coletor de Tempo Seco = ancora de receita da CTS
        if "liga" in n:   return "lig","coleta"        # Ligacao = ancora de receita
        if "rede" in n:   return "rede","rede"         # Rede coletora (coleta da propria sub-bacia)
        if "tronco" in n: return "tro","transporte"
        if "eee" in n or "elevat" in n: return "eee","transporte"
        return "lr","transporte"                       # Linha de Recalque
    obras=[]
    _div_capex=[]                                      # capex informado != quantidade x preco unitario
    for sb,lst in comp.items():
        if sb not in sis_de_sb: continue
        _ab=int(_anobase.get(sis_name[sis_de_sb[sb]],2026))
        so=subop.get(sb,{})
        lag=int(num(so.get("tempo_arrecadacao"),1))
        mat=int(num(so.get("tempo_ramp_up"),2))
        # SEM inadimplencia: a escolha faturada/arrecadada ja embute a arrecadacao
        adir=1.0; aind=1.0
        # ticket derivado = receita mensal media (faturada|arrecadada) / ligacoes atuais
        _rec_fat=num(so.get("receita_faturada_media_mensal"))
        _rec_arr=num(so.get("receita_arrecadada_media_mensal"))
        _rec_esc=_rec_fat if _BREC=="faturada" else _rec_arr
        _la_atu=num(so.get("ligacoes_atuais"))
        _ticket_der=(_rec_esc/_la_atu) if _la_atu>1e-9 else 0.0
        lig_novas=max(0.0,num(so.get("ligacoes_novas_obras")))   # ligacoes habilitadas pelas OBRAS -> RECEITA
        # O que a obra conta para a META. Igual ao de cima sem recorte; com recorte, so as
        # residenciais. A sub-bacia sem coluna residencial cai para o total.
        lig_cob=max(0.0,num(so.get(_COB_LIG[2]))) if _usa_res(sb) else lig_novas
        for x in lst:                                  # UMA OBRA POR COMPONENTE (desacoplados)
            nome=str(x.get("componente","")); pe=num(x.get("tempo_execucao"))
            # CAPEX pode vir DECOMPOSTO em quantidade x preco unitario; se vier, ele manda.
            _q=x.get("quantidade")
            _pu=x.get("preco_unitario")
            _un=x.get("unidade")
            if _q is not None and _pu is not None and str(_q).strip()!="" and str(_pu).strip()!="":
                _q=num(_q); _pu=num(_pu); cap=_q*_pu
                if x.get("capex") is not None and abs(num(x.get("capex"))-cap)>1.0:
                    _div_capex.append((sb,nome,num(x.get("capex")),cap))
            else:
                _q=_pu=None; cap=num(x.get("capex"))
            code,tipo=_codigo(nome); eh_lig=(tipo=="coleta"); necess=(cap>0 or pe>0)
            if not eh_lig and not necess: continue      # transporte/rede nao necessario -> ja tem capacidade
            if eh_lig and not necess and lig_novas<=0: continue
            kw=dict(no=sb,capex_comp={nome:cap},opex_ano=num(x.get("opex")),wacc=_wacc_fb(x.get("wacc"), sis_de_sb.get(sb)),
                    prazo_inicio=num(x.get("tempo_predecessoras")),prazo_exec=pe,
                    obrigatoria=cal2py(x.get("obra_obrigatoria_ano"), _ab),
                    proibida_ate=cal2py(x.get("obra_proibida_ate"), _ab))
            if eh_lig:
                kw.update(ligacoes=lig_novas,ligacoes_cobertura=lig_cob,ticket_mes=_ticket_der,
                          preco_ligacao=num(so.get("preco_por_ligacao")),arrec_dir=adir,arrec_ind=aind,lag=lag,maturacao=mat)
            _o=Obra(f"{code}_{sb}",tipo,**kw)
            _o.quantidade=_q; _o.preco_unitario=_pu
            _o.unidade=(str(_un).strip() if _un is not None and str(_un).strip()!="" else None)
            _o.wacc_origem=("proprio" if (x.get("wacc") is not None and str(x.get("wacc")).strip()!="")
                            else ("wacc_medio" if _o.wacc is not None else "ausente"))
            obras.append(_o)
    if _div_capex:
        print(f"  [aviso] {len(_div_capex)} componente(s) com 'capex' diferente de quantidade x preco_unitario "
              f"— prevalece o UNITARIO. Ex.: {[(a,b,f'{c:,.0f}->{d2:,.0f}') for a,b,c,d2 in _div_capex[:3]]}")
    _nq=sum(1 for o in obras if getattr(o,"quantidade",None) is not None)
    if _nq: print(f"  [info] CAPEX unitario: {_nq} de {len(obras)} componente(s) com quantidade x preco unitario")
    for sis,ete in ete_do_sis.items():
        d=etecap.get(ete,{}); _ab=int(_anobase.get(sis_name[sis],2026))
        eo=Obra("ete_"+ete,"ete",sistema=sis_name[sis],capex_comp={},wacc=_wacc_fb(d.get("wacc"), sis),opex_ano=num(d.get("opex_por_modulo")),
            prazo_inicio=num(d.get("tempo_predecessoras")),prazo_exec=num(d.get("tempo_de_execucao")),
            obrigatoria=cal2py(d.get("obra_obrigatoria_ano"), _ab),proibida_ate=cal2py(d.get("obra_proibida_ate"), _ab))
        eo.wacc_origem=("proprio" if (d.get("wacc") is not None and str(d.get("wacc")).strip()!="")
                        else ("wacc_medio" if eo.wacc is not None else "ausente"))
        eo.cap_modulo=num(d.get("capacidade_por_modulo"),0.0)   # vazao por modulo
        # A UNIDADE DA CAPACIDADE VEM DO CADASTRO, e nao e fixada aqui. A soma nao muda
        # com ela — o que muda e como o numero se le, e trocar a unidade de medida no
        # cadastro nao pode exigir mexer no motor nem na tela. Vazia = a rodada nao
        # declarou unidade, e quem mostra o numero mostra sem sufixo, em vez de inventar.
        eo.unidade_capacidade=(str(d.get("unidade_capacidade")).strip()
                               if str(d.get("unidade_capacidade") or "").strip() else None)
        eo.capex_modulo=num(d.get("capex_por_modulo"),0.0)      # CAPEX por modulo
        _oc=d.get("capacidade_ociosa")                                    # CAPACIDADE OCIOSA = nominal - vazao de operacao
        _nom=d.get("capacidade_nominal_atual"); _opv=d.get("vazao_de_operacao_atual")
        if _oc is None and _nom is not None: _oc=num(_nom)-num(_opv)
        eo.folga=max(0.0,num(_oc))
        if d.get("capacidade_ociosa") is not None and _nom is not None and abs(num(d.get("capacidade_ociosa"))-(num(_nom)-num(_opv)))>1e-6:
            print(f"  [aviso] ETE {ete}: capacidade_ociosa != capacidade_nominal_atual - vazao_de_operacao_atual")
        eo.capex_terreno=num(d.get("capex_terreno"),0.0)
        _flag_nova=str(d.get("nova","Nao")).strip().lower() in ("sim","s","true","1")
        eo.nova=_flag_nova or (eo.capex_terreno>1e-9)           # NOVA = flag 'nova=Sim' OU capex_terreno>0 (auto-deteccao)
        eo.modulos=int(round(num(d.get("modulos"),0)))          # nº de modulos DADO (ETE nova)
        if eo.nova: eo.folga=0.0                                # greenfield nao tem excedente (folga=0)
        eo.opex_por_modulo=num(d.get("opex_por_modulo"),0.0)   # OPEX por modulo (ETE faseada)
        eo.n_mod=0
        obras.append(eo)
    if _wm_used['n']: print(f"  [info] WACC da unidade (wacc_medio) aplicado a {_wm_used['n']} elemento(s) sem WACC contratado (financiamento nao atrelado).")
    modulos_sis={}
    if ete_faseada:                                     # cada ETE -> K modulos-OBRA (obras reais, priorizadas)
        _sf={}
        for sb,sis in sis_de_sb.items():
            _sf[sis_name[sis]]=_sf.get(sis_name[sis],0.0)+num(subop.get(sb,{}).get("vazao_contribuicao"))
        for eo in [o for o in obras if o.tipo=="ete"]:
            sisn=eo.sistema; eo.opex_ano=0.0            # ETE de referencia vira container (nao custa)
            lst=[]
            if getattr(eo,"nova",False):                # NOVA = PACOTE FIXO: todos os 'modulos' de uma vez; capacidade fixa
                eo.folga=0.0
                cap_total=eo.modulos*eo.cap_modulo
                capex_total=eo.capex_terreno+eo.modulos*eo.capex_modulo
                mo=Obra(f"{eo.id}#nova","ete_mod",sistema=sisn,capex_comp={f"ETE nova ({eo.modulos} mod + terreno)":capex_total},
                        opex_ano=eo.modulos*eo.opex_por_modulo,prazo_inicio=eo.prazo_inicio,prazo_exec=eo.prazo,
                        obrigatoria=getattr(eo,"obrig",0),proibida_ate=eo.proibida_ate,wacc=eo.wacc)   # ETE obrigatoria -> o pacote e obrigatorio
                mo.cap_modulo=cap_total; mo.folga=0.0; mo.modidx=1
                eo.cap_modulo=cap_total                  # o gating usa a capacidade TOTAL do pacote (teto de vazao)
                lst=[mo]; obras.append(mo)
            else:                                        # EXPANSAO: ramp de modulos conforme a vazao excede a folga
                exc=max(0.0,_sf.get(sisn,0.0)-eo.folga)
                K=int(math.ceil(exc/eo.cap_modulo)) if (exc>1e-9 and eo.cap_modulo>0) else (1 if exc>1e-9 else 0)
                for k in range(1,K+1):
                    mo=Obra(f"{eo.id}#m{k}","ete_mod",sistema=sisn,capex_comp={f"ETE modulo {k}":eo.capex_modulo},
                            opex_ano=eo.opex_por_modulo,prazo_inicio=eo.prazo_inicio,prazo_exec=eo.prazo,
                            obrigatoria=(getattr(eo,"obrig",0) if k==1 else 0),   # ETE obrigatoria -> 1o modulo obrigatorio; demais por demanda
                            proibida_ate=eo.proibida_ate,wacc=eo.wacc)
                    mo.cap_modulo=eo.cap_modulo; mo.folga=eo.folga; mo.modidx=k
                    lst.append(mo); obras.append(mo)
            modulos_sis[sisn]=lst
    _set_forma_adocao(curva_adocao)
    _ete_org={o.id:getattr(o,"wacc_origem","proprio") for o in obras if o.tipo=="ete"}
    for o in obras:
        if o.tipo=="ete_mod": o.wacc_origem=_ete_org.get(str(o.id).split("#")[0], getattr(o,"wacc_origem","proprio"))
    cen=Cenario(nos,cidades,obras,orc,hz=hz)
    cen.modulos_sis=modulos_sis
    cen.vazao={sb:num(subop.get(sb,{}).get("vazao_contribuicao")) for sb in sis_de_sb}
    cen.sub_receita={}                                    # base (existente) p/ relatorio de receita total
    for _sb in sis_de_sb:
        _so=subop.get(_sb,{})
        _rEsc=(num(_so.get("receita_faturada_media_mensal")) if _BREC=="faturada"
               else num(_so.get("receita_arrecadada_media_mensal")))
        _lAt=num(_so.get("ligacoes_atuais"))
        cen.sub_receita[_sb]={"atuais":_lAt,"ticket":((_rEsc/_lAt) if _lAt>1e-9 else 0.0),
                              "arrec":1.0,"base_receita":_BREC}
    if _orc_cal:                       # o cronograma de orcamento define a janela de CAPEX
        _abref=min(_anobase.values()) if _anobase else 2026
        cen.anos_capex=max(1, max(orcamento)-_abref+1)
    else:
        cen.anos_capex=int(horizonte_capex) if horizonte_capex else cen.anos   # janela de INICIO de obras
    # --- DATA DE INICIO DAS OBRAS: nada pode comecar antes dela (1o ano-calendario fica parcial) ---
    _abref=min(_anobase.values()) if _anobase else 2026
    _off=0
    if data_inicio is not None:
        if isinstance(data_inicio,(list,tuple)): _mi,_ai=int(data_inicio[0]),int(data_inicio[1])
        else:
            _p=str(data_inicio).replace("/","-").split("-"); _mi,_ai=int(_p[0]),int(_p[1])
        _off=max(0,(_ai-_abref)*12+(_mi-1))
    cen.mes_inicio=_off                                       # mes interno 0-based do inicio (0 = jan do ano_base)
    if _off>0:
        for _o in cen.obras.values(): _o.inicio_min=max(int(_o.inicio_min),_off)
        print(f"  [inicio] obras a partir do mes interno {_off} = {_mi:02d}/{_ai} (o 1o ano fica com {12-(_off%12) if _off%12 else 12} meses)")
    cen.orc_total=float(orcamento_total) if orcamento_total else None   # teto TOTAL da janela (otimizador distribui os anos)
    cen.anos_extra=max(0,int(anos_extra_conclusao))                    # anos APOS a janela em que uma obra iniciada pode CONCLUIR
    cen.orc_janela_total={_rg:sum(cen.orc[_rg][:int(cen.anos_capex)]) for _rg in cen.regionais}  # sobra acumulada da janela custeia o rabo
    cen.ete_fixo=bool(ete_fixo); cen.ete_faseada=bool(ete_faseada)
    # --- COBERTURA (ligacoes TRATADAS) por SISTEMA: total possivel e base atual ---
    # --- UNIDADE DE COBERTURA por CIDADE (coluna 'unidade_cobertura' em cidade-operacional):
    #     ligacoes (default) | economias | populacao. Converte universo/base/incrementos por densidade.
    _unid={}
    for _c,_d in cidop.items():
        _u=str(_d.get("unidade_cobertura") or "ligacoes").strip().lower()
        _unid[cid_name.get(_c,_c)]=("economias" if _u.startswith("econ") else ("populacao" if _u.startswith("pop") else "ligacoes"))
    _ufat={}; _sem_pop=[]
    for _sb2,_sis2 in sis_de_sb.items():
        _cn2=cid_name[sis_cid[_sis2]]; _u2=_unid.get(_cn2,"ligacoes"); _so2=subop.get(_sb2,{})
        _dd=_dens_sb.get(_sb2,{})
        # Com o recorte ligado a conversao usa a densidade RESIDENCIAL: o numerador e o
        # denominador ja estao em ligacoes residenciais.
        if   _u2=="economias" and _usa_res(_sb2): _f2=(_dd.get("economias_res") or 0.0) or 1.0
        elif _u2=="economias": _f2=(_dd.get("economias") or 0.0) or 1.0
        elif _u2=="populacao": _f2=(_dd.get("populacao") or 0.0) or 1.0
        else: _f2=1.0
        if _u2=="populacao" and not (_dd.get("populacao") or 0.0):
            _sem_pop.append(_sb2)
        _ufat[_sb2]=_f2
    if _sem_pop:
        print(f"  [aviso] {len(_sem_pop)} sub-bacia(s) em cidade com unidade de cobertura POPULACAO mas sem "
              f"populacao informada — a cobertura delas cai para LIGACOES. Ex.: {_sem_pop[:3]}")
    _nl={_u:sum(1 for _c in _unid.values() if _c==_u) for _u in set(_unid.values())}
    if any(_u!="ligacoes" for _u in _unid.values()):
        print(f"  [info] unidade de COBERTURA por cidade: {_nl} (meta e paridade medidas nessa unidade; receita segue em ligacoes)")
    # POTENCIAL DE CRESCIMENTO: fator multiplicador do UNIVERSO da sub-bacia (default 1.0).
    #   1.0 = sem crescimento · 1.5 = universo 50% maior (nova ocupacao, adensamento futuro).
    #   Afeta SO o universo (denominador da meta): base atendida e ligacoes novas nao mudam,
    #   e a densidade derivada tambem nao (o fator entra na agregacao, nao no dado bruto).
    def _pot(_d):
        _p=_d.get("potencial_crescimento", _d.get("fator_crescimento", _d.get("potencial")))
        try:
            _p=float(_p)
            return _p if _p>0 else 1.0
        except (TypeError, ValueError):
            return 1.0
    maxlig={}; baselig={}; _aviso_univ=[]; _pot_sb={}; _n_pot=0
    for _sb,_lst in comp.items():
        if _sb not in sis_de_sb: continue
        _sn=cid_name[sis_cid[sis_de_sb[_sb]]]                              # agrega por CIDADE
        _so=subop.get(_sb,{})
        # O TRIPLO DA COBERTURA. Com o recorte ligado sao as colunas residenciais; a
        # sub-bacia que nao tiver a residencial cai para a total, que e a degradacao
        # anunciada no aviso la de cima.
        _cu,_ca,_cn=(_COB_LIG if _usa_res(_sb)
                     else ("universo_ligacoes","ligacoes_atuais","ligacoes_novas_obras"))
        _un=_so.get(_cu)                                                  # UNIVERSO (denominador da meta)
        _la=_so.get(_ca)                                                  # ligacoes ja atendidas (base)
        _no=num(_so.get(_cn,0))
        _pt=_pot(_so); _pot_sb[_sb]=_pt
        if _pt!=1.0: _n_pot+=1
        _un_ef=num(_un)*_pt                                              # universo COM potencial de crescimento
        if _un_ef+1e-6 < num(_la)+_no: _aviso_univ.append(_sb)          # universo(efetivo) < atuais+novas (dado inconsistente)
        _fu2=_ufat.get(_sb,1.0)                                          # -> unidade de cobertura da cidade
        maxlig[_sn]=maxlig.get(_sn,0.0)+_un_ef*_fu2
        baselig[_sn]=baselig.get(_sn,0.0)+num(_la)*_fu2                  # base NAO cresce
    if _aviso_univ: print(f"  [aviso] {len(_aviso_univ)} sub-bacia(s) com universo (x potencial) < atuais+novas_obras (ex.: {_aviso_univ[:3]})")
    if _n_pot: print(f"  [info] potencial de crescimento > 1 em {_n_pot} sub-bacia(s): universo da meta ampliado")
    cen.max_lig=maxlig; cen.base_lig=baselig; cen.ano_base=_anobase; cen.potencial_crescimento=_pot_sb
    cen.unid_fator=_ufat; cen.unidade_cobertura=_unid
    cen.densidade=_dens_sb                     # {sub-bacia: {economias: x, populacao: y}} — DERIVADO
    # rotulos do escopo (o eixo interno 'regional' do cenario carrega a UNIDADE)
    cen.unidade_nome=(list(uni_name.values())[0] if len(uni_name)==1 else None)
    cen.unidade_id=(list(uni_name)[0] if len(uni_name)==1 else None)
    cen.regional_nome=(list(reg_name.values())[0] if len(reg_name)==1 else None)
    cen.eixo_orcamento="unidade"
    cen.curva_adocao=_FORMA_ADOCAO
    cen.base_receita=_BREC
    _metas={}
    if metas_cobertura is None:
        try: _rows=L("metas-cobertura")
        except Exception: _rows=[]
        _fora_esc=0
        for d in _rows:
            _c=d.get("cidade_id") or d.get("cidade") or d.get("sistema_id")            # metas por CIDADE (fallback compat)
            if _c is None: continue
            if _c not in cid_name and _c not in sis_name:      # cidade de OUTRO escopo -> ignora
                _fora_esc+=1; continue
            _cn=cid_name.get(_c, sis_name.get(_c,_c))
            _metas.setdefault(_cn,{})[int(num(d.get("ano")))]=num(d.get("cobertura_pct") or d.get("pct"))
    elif isinstance(metas_cobertura,dict):
        for _s,_al in metas_cobertura.items():
            _it=_al.items() if isinstance(_al,dict) else _al
            for _a,_p in _it: _metas.setdefault(_s,{})[int(_a)]=float(_p)
    if metas_cobertura is None and _fora_esc:
        print(f"  [info] {_fora_esc} meta(s) de cidades fora do escopo selecionado — ignoradas")
    for _s in _metas:                                     # pct em % (>1) -> fracao
        for _a in list(_metas[_s]):
            _p=_metas[_s][_a]; _metas[_s][_a]=(_p/100.0 if _p>1.0 else _p)
    cen.metas_cobertura=_metas
    # FATOR DE EQUIVALENCIA ESGOTO/AGUA por FAIXA DE COBERTURA (aba 'fator-esgoto': cidade_id, cobertura_pct, fator)
    _fe={}
    _rowsf=[]
    for _abaf in ("fator-esgoto","paridade","paridade-esgoto"):      # aceita os nomes de aba
        try:
            _rowsf=L(_abaf)
            if _rowsf: break
        except Exception: _rowsf=[]
    _par_fora=0
    for d in _rowsf:
        _c=d.get("cidade_id") or d.get("cidade")
        if _c is None: continue
        if _c not in cid_name:                             # paridade de OUTRO escopo -> ignora
            _par_fora+=1; continue
        _cn=cid_name.get(_c,_c)
        _cb=num(d.get("cobertura_pct") or d.get("cobertura")); _fv=num(d.get("paridade") or d.get("fator") or d.get("fator_esgoto"))   # coluna PARIDADE (fallback: fator)
        if _fv<=0: continue
        _fe.setdefault(_cn,[]).append((( _cb/100.0 if _cb>1.0 else _cb), float(_fv)))
    for _c in _fe: _fe[_c]=sorted(_fe[_c])
    cen.fator_esgoto=_fe
    if _fe:
        print(f"  [info] PARIDADE esgoto/agua: faixas carregadas para {len(_fe)} cidade(s)"
              + (f" ({_par_fora} linha(s) de fora do escopo ignoradas)" if _par_fora else ""))
    # PESO POR CIDADE (multiplica a importancia de bater a meta/cobertura daquela cidade). Default 1.0.
    _pcid={}
    if peso_cidade:
        _alias={cid_name.get(k,k):k for k in cid_name}   # aceita id ou nome de cidade
        for k,v in dict(peso_cidade).items():
            nome=cid_name.get(k, k)                        # se veio id -> nome; se ja e nome, mantem
            _pcid[str(nome)]=float(v)
    cen.peso_cidade=_pcid
    # foco_cobertura in [0,1]: 0=so VPL, 1=so cobertura. Converte para peso R$/ligacao auto-calibrado.
    cen.penalidade_cobertura=str(penalidade_cobertura or "meta+cobertura").lower()   # "meta+cobertura" | "meta" | "ligacao"
    cen.total_max_lig=sum(maxlig.values())
    _capex_tot=sum(getattr(o,"capex",0.0) for o in obras)
    _n_metas=sum(len(al) for al in _metas.values()) if _metas else 0
    _lig_meta=sum(maxlig.get(_s,0.0) for _s in _metas) if _metas else sum(maxlig.values())
    _divisor=max(1.0, _lig_meta if cen.penalidade_cobertura=="ligacao" else _n_metas)
    _Lam0=_capex_tot/_divisor          # CAPEX por META (ou por ligacao) = ponto de equilibrio foco=0.5
    if foco_cobertura is not None:
        _a=min(1.0,max(0.0,float(foco_cobertura)))
        _cap=_capex_tot*10.0                      # foco->1: multa domina qualquer VPL (cobertura pura)
        cen.peso_cobertura=min(_Lam0*_a/(1.0-_a+1e-9), _cap); cen.foco_cobertura=_a
    else:
        cen.peso_cobertura=float(peso_cobertura); cen.foco_cobertura=None
    if _metas:
        _abref=min(_anobase.values()) if _anobase else 2026; _hlast=_abref+int(cen.anos_capex)-1
        _fora=sorted({a for al in _metas.values() for a in al if a>_hlast})
        if _fora: print(f"  [aviso] metas em anos ALEM do horizonte de CAPEX (>{_hlast}) IGNORADAS: {_fora}")
    if (cen.peso_cobertura>0) and _metas:
        _todos=set(n.cidade for n in cen.nos.values()); _sem=sorted(_todos-set(_metas))
        if _sem: print(f"  [aviso] {len(_sem)} de {len(_todos)} cidade(s) SEM meta de cobertura (ex.: {_sem[:3]}) - sob a lei todos deveriam ter")
    if ete_fixo:                                    # pre-dimensiona a ETE (para a vazao TOTAL do sistema) -> CAPEX FIXO
        sbmap={}
        for n in cen.nos.values(): sbmap.setdefault(n.sistema,[]).append(n.id)
        for e in cen.ete_do_sistema.values():
            tot=sum(cen.vazao.get(sb,0.0) for sb in sbmap.get(e.sistema,[]))
            _opm=getattr(e,"opex_por_modulo",e.opex_ano)
            if getattr(e,"nova",False):
                e.capex_fixo=e.capex_terreno+e.modulos*e.capex_modulo; e.opex_ano=e.modulos*_opm
            else:
                exc=max(0.0,tot-e.folga)
                nn=int(math.ceil(exc/e.cap_modulo)) if (exc>1e-9 and e.cap_modulo>0) else (1 if exc>1e-9 else 0)
                e.capex_fixo=nn*e.capex_modulo; e.opex_ano=nn*_opm
    cen.usar_cts=bool(usar_cts)
    # cts_ids = SO as CTS efetivamente carregadas nesta unidade (interseccao com os nos), nao a aba inteira
    cen.cts_ids=(set(_cts_op) & set(cen.nos)) if usar_cts else set()
    for _n in cen.nos.values(): _n.is_cts=(_n.id in cen.cts_ids)
    _cts_na_uni={_c for _s,_c in _cts_dep.items() if _s in cen.nos}   # pares cuja sub-bacia esta no escopo
    if _cts_na_uni:
        # A descricao do modo desligado mudou junto com o comportamento: ela dizia
        # "demanda somada", e somar era exatamente o que passou a NAO acontecer quando ha
        # coluna consolidada. Log que descreve o codigo antigo e pior que log nenhum.
        print(f"  [info] CTS: {len(_cts_na_uni)} nesta unidade ({len(_cts_ids_all)} no banco) -> usar_cts={usar_cts} "
              f"({'entram como nos proprios' if usar_cts else 'a sub-bacia absorve a pareada (colunas *_com_cts)'})")
    # RECORTADOS PELA UNIDADE. `cen.nos` e o mesmo filtro que a linha acima usa — sem ele
    # o aviso falaria do banco inteiro, inclusive numa unidade sem CTS nenhuma.
    _abs_uni=[_sb for _sb in _absorvidas if _sb in cen.nos]
    _sem_uni=[_sb for _sb in _sem_consolidado if _sb in cen.nos]
    if _abs_uni:
        print(f"  [aviso] {len(_abs_uni)} sub-bacia(s) desta unidade com CTS pareada: sem o coletor, "
              f"VAZAO, RECEITA e POPULACAO usadas sao as da BASE DA SUB-BACIA — a linha da CTS nao "
              f"entra. Se desligar o coletor muda a vazao, a base precisa refletir isso.")
    if _sem_uni:
        print(f"  [ALERTA] {len(_sem_uni)} sub-bacia(s) desta unidade com CTS pareada mas SEM "
              f"`universo_ligacoes_com_cts`: a rodada sem coletor usou o universo EXCLUSIVO delas, "
              f"ou seja, ignorou a area sobreposta. Ex.: {_sem_uni[:3]}")
    return cen
