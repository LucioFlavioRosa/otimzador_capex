"""EXPERIMENTOS LOCAIS — roda o otimizador na sua maquina, sem Databricks e sem Postgres.

O motor e o solver sao Python puro: recebem as abas e devolvem o plano. Nada aqui precisa de
cluster, banco ou credencial. E o jeito mais rapido de entender o que o otimizador faz e de
testar hipoteses ("e se o orcamento cair pela metade?", "quanto a CTS custa em VPL?").

    python main.py experimento                          # uma rodada com o banco de teste
    python main.py experimento --orcamento 15e6         # teto anual de R$ 15 mi
    python main.py experimento --foco-cobertura 0.7     # priorizando cobertura
    python main.py experimento --sem-cts --so-residencial
    python main.py experimento --salvar resultados/     # grava as 14 tabelas em CSV

    python main.py experimento --comparar foco          # varre o foco e compara
    python main.py experimento --comparar orcamento     # varre o teto e compara
    python main.py experimento --comparar cts           # com CTS x sem CTS
    python main.py experimento --comparar industrial    # com x sem parcela industrial

    python main.py experimento --listar-bancos          # os bancos disponiveis

O passo a passo completo, do zero ate rodar o job inteiro contra um Postgres local, esta em
docs/07-rodar-local.md.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import time

import matplotlib
matplotlib.use("Agg")                     # o dashboard importa pyplot; sem janela grafica

# raiz do REPO (este arquivo vive em otimizador/aplicacao/): e dela que saem os caminhos
# relativos dos bancos de teste (tests/fixtures) e das pastas de saida.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURES = os.path.join("tests", "fixtures")
BANCOS = {
    "cts":      (os.path.join(FIXTURES, "banco_teste_CTS_poc_v2.json"),
                 "2 cidades, 4 sub-bacias, 2 CTS. O default."),
    "sem-cts":  (os.path.join(FIXTURES, "banco_fixture_testes.json"),
                 "sem CTS, com mix de WACC (parte herda o wacc_medio da unidade)."),
    "classe":   (os.path.join(FIXTURES, "banco_fixture_classe.json"),
                 "com parcela industrial; c1 mede cobertura em economias, c2 em populacao."),
}


# --------------------------------------------------------------------- motor
def _modulos():
    from otimizador.apresentacao import dashboard_otimizador_v2 as D
    from otimizador.dominio import otimizador_capex_v62 as M
    from otimizador.dominio import qualidade as Q
    from otimizador.infraestrutura import persistencia as P
    D.set_engine(M)
    P.set_engine(M, D)
    return M, P, Q


def _silencioso(verboso):
    """O motor imprime diagnosticos na carga; util com --verboso, ruido no resto."""
    return contextlib.nullcontext() if verboso else contextlib.redirect_stdout(io.StringIO())


def _carregar(banco, verboso=False, **params):
    """As abas de um banco de experimento (JSON) -> Cenario.

    O motor recebe ABAS, nao arquivo. Aqui a fonte e um JSON de fixture; em producao e o
    Postgres (`carregar_postgres.abas_do_postgres`). Duas fontes, a mesma porta."""
    import json
    M, _, _ = _modulos()
    caminho = banco if os.path.isabs(banco) else os.path.join(ROOT, banco)
    if not os.path.exists(caminho):
        raise SystemExit(f"banco nao encontrado: {caminho}\n"
                         f"  use --listar-bancos para ver os disponiveis")
    with open(caminho, encoding="utf-8") as f:
        abas = json.load(f)
    with _silencioso(verboso):
        return M.ler_banco(abas, **params)


def _resolver(cen, max_time_s, workers, build_all, verboso=False):
    """Devolve (res, segundos). `build_all` constroi TUDO no inicio: nao e otimizacao, mas e
    deterministico e instantaneo — serve de piso de comparacao (o solver nunca fica pior)."""
    M, _, _ = _modulos()
    t0 = time.perf_counter()
    with _silencioso(verboso):
        if build_all:
            plano = {oid: max(0, int(o.inicio_min))
                     for oid, o in cen.obras.items() if o.eh_aegea()}
            res = M.avaliar(cen, plano)
            res.setdefault("milp_status", "BUILD-ALL (sem solver)")
        else:
            from otimizador.dominio import otimizador_capex_cpsat63 as CP
            res = CP.resolver_por_sistema(cen, max_time_s=max_time_s, workers=workers)
    return res, time.perf_counter() - t0


def _cobertura_final(tabs):
    """Cobertura da unidade, ponderada pelo universo de cada cidade.

    `run_meta.cobertura_final_pct` e coluna de SERVICO: quem a preenche e
    `publicacao._garantir_colunas_servico`, no momento de publicar. Numa rodada local ela
    vem vazia — entao calculamos aqui, com a mesma formula."""
    cid = tabs.get("run_cidade")
    if cid is None or not len(cid) or "universo" not in cid.columns:
        return 0.0
    u = cid["universo"].fillna(0)
    total = float(u.sum())
    if not total:
        return 0.0
    return float((cid["cobertura_final_pct"].fillna(0) * u).sum() / total)


def _kpis(res, tabs, segundos):
    m = tabs["run_meta"].iloc[0]
    return {
        "status": str(res.get("milp_status", "?"))[:34],
        "vpl": float(m.get("vpl") or 0.0),
        "capex": float(m.get("capex_total") or 0.0),
        "obras": f"{int(m.get('obras_construidas') or 0)}/{int(m.get('obras_total') or 0)}",
        "faturando": f"{int(m.get('subbacias_faturando') or 0)}/{int(m.get('subbacias_total') or 0)}",
        "cobertura": _cobertura_final(tabs),
        "metas_nao": int(m.get("metas_nao_atingidas") or 0),
        "metas": int(m.get("metas_total") or 0),
        "segundos": segundos,
    }


def _rodada(banco, params, max_time_s, workers, build_all, verboso=False):
    """Carrega -> resolve -> materializa. Devolve (cen, res, tabs, kpis)."""
    _, P, _ = _modulos()
    cen = _carregar(banco, verboso=verboso, **params)
    res, seg = _resolver(cen, max_time_s, workers, build_all, verboso)
    with _silencioso(verboso):
        # `abas_fonte`: sem ele o snapshot__* nao e gerado, e `--salvar` gravaria as run_*
        # sem a copia congelada do banco de entrada — justamente o que a mensagem promete.
        import json
        caminho = banco if os.path.isabs(banco) else os.path.join(ROOT, banco)
        with open(caminho, encoding="utf-8") as f:
            abas = json.load(f)
        tabs = P.materializar(cen, res, run_id="experimento_local",
                              banco=os.path.basename(banco), abas_fonte=abas,
                              params=params)
    return cen, res, tabs, _kpis(res, tabs, seg)


# --------------------------------------------------------------------- saida
def _reais(v):
    return f"R$ {v:,.0f}".replace(",", ".")


def _mostrar(kpis, params, banco):
    print("=" * 74)
    print(f"  {os.path.basename(banco)}   |   " +
          "  ".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None))
    print("=" * 74)
    metas = f"{kpis['metas_nao']}/{kpis['metas']}"
    tempo = f"{kpis['segundos']:.1f}s"
    cobertura = f"{kpis['cobertura']:.1f}%"
    print(f"  status do solver     {kpis['status']}")
    print(f"  VPL                  {_reais(kpis['vpl']):>22}")
    print(f"  CAPEX                {_reais(kpis['capex']):>22}")
    print(f"  obras construidas    {kpis['obras']:>22}")
    print(f"  sub-bacias faturando {kpis['faturando']:>22}")
    print(f"  cobertura final      {cobertura:>22}")
    print(f"  metas nao atingidas  {metas:>22}")
    print(f"  tempo                {tempo:>22}")


def _portao(cen, res, tabs, build_all=False):
    _, _, Q = _modulos()
    if build_all:
        print("  NOTA: com --build-all a checagem 'Status do solver' reprova por construcao"
              " — nao houve solver. As outras 13 valem normalmente.\n")
    ok, rel, resumo = Q.checar(cen, res, tabs)
    Q.imprimir(rel, resumo)
    if build_all:
        criticas = [r for r in rel if r["nivel"] == "critico" and not r["ok"]]
        return len(criticas) == 1 and criticas[0]["check"] == "Status do solver"
    return ok


# ---------------------------------------------------------------- comparacao
def _variacoes(dimensao, base):
    """Devolve [(rotulo, params_extras)] para a dimensao escolhida."""
    if dimensao == "foco":
        return [(f"foco={f}", {"foco_cobertura": f}) for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    if dimensao == "orcamento":
        # A faixa vem do PROPRIO cenario: fracoes do PICO ANUAL de CAPEX do plano que
        # constroi tudo. Esse pico e o teto minimo que ainda permite fazer tudo no ritmo
        # ideal — abaixo dele o solver precisa adiar ou largar obra, que e o que queremos
        # observar. (Dividir o CAPEX total pelo horizonte daria um teto pequeno demais: o
        # investimento e concentrado no inicio, nao espalhado pelos 20 anos.)
        orc = base["_teto_base"]
        return [(f"{int(m*100)}% do pico ({_reais(orc * m)})", {"orcamento": orc * m})
                for m in (0.25, 0.5, 0.75, 1.0)]
    if dimensao == "cts":
        return [("com CTS", {"usar_cts": True}), ("sem CTS", {"usar_cts": False})]
    if dimensao == "industrial":
        return [("cobertura total", {"cobertura_so_residencial": False}),
                ("cobertura so residencial", {"cobertura_so_residencial": True})]
    raise SystemExit(f"dimensao desconhecida: {dimensao} "
                     f"(use: foco, orcamento, cts, industrial)")


def _comparar(dimensao, banco, params, max_time_s, workers, build_all):
    if dimensao == "orcamento" and build_all:
        # `avaliar` (build-all) constroi tudo e IGNORA o teto: todas as linhas sairiam
        # identicas. Comparar orcamento so faz sentido com o solver, que respeita o teto.
        raise SystemExit("--comparar orcamento nao funciona com --build-all: o build-all\n"
                         "  constroi tudo e ignora o teto, entao todos os cenarios dariam o\n"
                         "  mesmo numero. Rode sem --build-all (usa o solver).")
    print(f"\nComparando '{dimensao}' em {os.path.basename(banco)} "
          f"({'build-all' if build_all else 'solver'})\n")

    # sonda: quanto custa construir TUDO, e em quantos anos de janela. Serve de escala para
    # o teto (comparar orcamento) e para avisar quando o teto nao esta restringindo nada.
    _, _, tabs0, k0 = _rodada(banco, params, max_time_s, workers, build_all=True)
    pico = float(tabs0["run_ano"]["capex"].max() or 0.0) or k0["capex"]
    params = dict(params, _teto_base=pico)
    print(f"  (construir tudo custa {_reais(k0['capex'])}, com pico de {_reais(pico)}"
          f" num unico ano)\n")

    cab = f"  {'cenario':<28}{'VPL':>18}{'CAPEX':>18}{'obras':>9}{'cobert.':>9}{'metas!':>8}{'seg':>7}"
    print(cab)
    print("  " + "-" * (len(cab) - 2))
    linhas = []
    for rotulo, extra in _variacoes(dimensao, params):
        p = {k: v for k, v in params.items() if not k.startswith("_")}
        p.update(extra)
        try:
            _, _, _, k = _rodada(banco, p, max_time_s, workers, build_all)
        except Exception as e:
            print(f"  {rotulo:<28}  FALHOU: {type(e).__name__}: {str(e)[:40]}")
            continue
        linhas.append((rotulo, k))
        print(f"  {rotulo:<28}{_reais(k['vpl']):>18}{_reais(k['capex']):>18}"
              f"{k['obras']:>9}{k['cobertura']:>8.1f}%{k['metas_nao']:>8}{k['segundos']:>7.1f}")
    if len(linhas) >= 2 and len({round(k["vpl"], 2) for _, k in linhas}) == 1:
        construidas = {k["obras"].split("/")[0] for _, k in linhas}
        if construidas == {"0"}:
            print("\n  Nenhum cenario construiu nada: o teto esta apertado demais para caber"
                  "\n  qualquer obra. Suba o --orcamento.")
        else:
            print("\n  Todos os cenarios deram o MESMO resultado — o teto nao esta restringindo:"
                  "\n  se o plano inteiro cabe no orcamento, o solver constroi tudo e nenhum"
                  "\n  outro parametro muda a escolha. Baixe o --orcamento para ver o efeito.")
    if len(linhas) >= 2:
        melhor = max(linhas, key=lambda x: x[1]["vpl"])
        maior_cob = max(linhas, key=lambda x: x[1]["cobertura"])
        print()
        print(f"  maior VPL:       {melhor[0]}  ({_reais(melhor[1]['vpl'])})")
        print(f"  maior cobertura: {maior_cob[0]}  ({maior_cob[1]['cobertura']:.1f}%)")
        # so e trade-off de verdade se os dois criterios discordarem NOS VALORES; rotulos
        # diferentes com valores empatados sao apenas o primeiro maximo de cada lista.
        if (melhor[1]["cobertura"] < maior_cob[1]["cobertura"]
                and maior_cob[1]["vpl"] < melhor[1]["vpl"]):
            print("  -> trade-off real: o cenario de maior VPL nao e o de maior cobertura.")
    print()


# --------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Roda o otimizador localmente, sem Databricks e sem Postgres.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--banco", default="cts",
                    help="apelido (cts, sem-cts, classe) ou caminho de um .json de abas")
    ap.add_argument("--listar-bancos", action="store_true")

    ap.add_argument("--orcamento", type=float, default=20e6,
                    help="teto ANUAL de CAPEX, em reais (default: 20e6)")
    ap.add_argument("--foco-cobertura", type=float, default=None,
                    help="0 = so VPL · 1 = so cobertura. Omitido = default do motor (so VPL)")
    ap.add_argument("--unidade", default=None)
    ap.add_argument("--base-receita", default=None, choices=["arrecadada", "faturada"])
    ap.add_argument("--sem-cts", action="store_true", help="usar_cts=False")
    ap.add_argument("--so-residencial", action="store_true", help="cobertura_so_residencial=True")
    ap.add_argument("--ete-faseada", action="store_true", help="cada ETE vira K obras-modulo")

    ap.add_argument("--build-all", action="store_true",
                    help="constroi tudo no inicio, sem solver (instantaneo, deterministico)")
    ap.add_argument("--max-time", type=int, default=60, help="segundos do solver (default: 60)")
    ap.add_argument("--workers", type=int, default=8)

    ap.add_argument("--comparar", metavar="DIMENSAO",
                    help="varre uma dimensao e compara: foco | orcamento | cts | industrial")
    ap.add_argument("--salvar", metavar="PASTA", help="grava as 14 tabelas nessa pasta")
    ap.add_argument("--formato", default="csv", choices=["csv", "parquet"])
    ap.add_argument("--sem-portao", action="store_true", help="nao roda o portao de qualidade")
    ap.add_argument("--detalhe", action="store_true", help="imprime o plano obra a obra")
    ap.add_argument("--verboso", action="store_true", help="mostra os diagnosticos do motor")
    a = ap.parse_args(argv)

    if a.listar_bancos:
        print("\nBancos que acompanham o pacote:\n")
        for apelido, (caminho, desc) in BANCOS.items():
            existe = "ok " if os.path.exists(os.path.join(ROOT, caminho)) else "AUSENTE"
            print(f"  [{existe}] {apelido:<10} {caminho}\n             {desc}")
        print("\nOu passe o caminho de um .json de abas proprio em --banco.\n")
        return 0

    banco = BANCOS[a.banco][0] if a.banco in BANCOS else a.banco

    params = {"orcamento": a.orcamento}
    if a.foco_cobertura is not None:
        params["foco_cobertura"] = a.foco_cobertura
    if a.unidade:
        params["unidade"] = a.unidade
    if a.base_receita:
        params["base_receita"] = a.base_receita
    if a.sem_cts:
        params["usar_cts"] = False
    if a.so_residencial:
        params["cobertura_so_residencial"] = True
    if a.ete_faseada:
        params["ete_faseada"] = True

    if a.comparar:
        _comparar(a.comparar, banco, params, a.max_time, a.workers, a.build_all)
        return 0

    cen, res, tabs, kpis = _rodada(banco, params, a.max_time, a.workers,
                                   a.build_all, a.verboso)
    print()
    _mostrar(kpis, params, banco)

    if a.detalhe:
        M, _, _ = _modulos()
        print()
        M.imprimir(cen, res, "PLANO")

    ok = True
    if not a.sem_portao:
        print()
        ok = _portao(cen, res, tabs, build_all=a.build_all)

    if a.salvar:
        _, P, _ = _modulos()
        pasta = a.salvar if os.path.isabs(a.salvar) else os.path.join(ROOT, a.salvar)
        P.salvar(tabs, pasta, formato=a.formato, verbose=True)
        print(f"\n  tabelas em {pasta} — uma pasta por tabela, {a.formato} dentro.")
        print("  as snapshot__* sao a copia congelada do banco de entrada.")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
