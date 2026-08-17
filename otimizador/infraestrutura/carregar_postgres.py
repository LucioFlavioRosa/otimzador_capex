"""FASE 2 — Adaptador de dados: Postgres -> Cenario.

Objetivo: ler as tabelas de INPUT do Postgres (o cadastro que o front grava) e produzir
o MESMO objeto `Cenario` que `ler_banco(<xlsx>)` produz. O motor nao muda e nao sabe de
onde vieram os dados.

Estrategia (baixo risco, 100% reuso do motor): materializa as tabelas do Postgres em um
.xlsx temporario com os MESMOS nomes de aba/coluna e chama `ler_banco`. Assim todo o
comportamento (derivacao de novas, CTS, industrial, cobertura por unidade, avisos) e
IDENTICO ao caminho Excel — nao ha logica duplicada para manter.

Evolucao "limpa" (Fase 2b, opcional): trocar `ler_banco(path)` por um `ler_banco(fonte)`
que aceite um dict de DataFrames direto, eliminando o arquivo temporario. Fica para depois
que o caminho estiver validado — o adaptador abaixo ja entrega valor sem tocar no motor.

    from otimizador.infraestrutura.carregar_postgres import carregar_postgres
    cen = carregar_postgres(pg_url, schema="input", **params_da_rodada)
"""
from __future__ import annotations
import os
import tempfile

# aba (nome usado pelo motor)  ->  tabela no Postgres (schema `input`)
# a chave e o nome EXATO da aba que o ler_banco espera; o valor e a tabela fisica.
ABAS_INPUT = {
    "unidade-regional":            "unidade_regional",
    "regional-superintendencia":   "regional_superintendencia",
    "superintendencia-cidade":     "superintendencia_cidade",
    "cidade-sistema":              "cidade_sistema",
    "sistema-topologia":           "sistema_topologia",
    "cidade-operacional":          "cidade_operacional",
    "subbacia-operacional":        "subbacia_operacional",
    "componentes-subbacias-capex": "componentes_subbacias_capex",
    "ete-capex":                   "ete_capex",
    "regional-operacional":        "regional_operacional",
    "metas-cobertura":             "metas_cobertura",
    "fator-esgoto":                "fator_esgoto",
    # CTS (opcionais — so existem se a unidade tiver CTS)
    "subbacia-cts":                "subbacia_cts",
    "cts-operacional":             "cts_operacional",
    "componentes-cts-capex":       "componentes_cts_capex",
    # teto de CAPEX por regional/unidade — o motor le esta aba como FALLBACK quando
    # ORCAMENTO nao vem no run_request (v62: orc_reg). Sem ela e sem o parametro, o
    # motor usa INF e o plano sai sem teto.
    "orcamento":                   "orcamento",
}

# abas que podem legitimamente NAO existir no banco (a unidade nao tem CTS; o orcamento
# veio por parametro; `sistema-operacional` so existe em bancos antigos).
ABAS_OPCIONAIS = {"subbacia-cts", "cts-operacional", "componentes-cts-capex",
                  "orcamento", "sistema-operacional"}

# Abas cujo conteudo VAZIO e sempre erro: sem elas o Cenario nao faz sentido. As que ficam
# de fora desta lista podem vir vazias — o motor tolera (`fator_esgoto` sem faixas cai em
# paridade 1.0, `otimizador_capex_v62.py:365`; `metas_cobertura` vazio = rodada so por VPL;
# `ete_capex` vazio = sistema sem ETE) — mas a carga avisa em voz alta.
ABAS_ESTRUTURAIS = {"unidade-regional", "regional-superintendencia", "superintendencia-cidade",
                    "cidade-sistema", "sistema-topologia", "cidade-operacional",
                    "subbacia-operacional", "componentes-subbacias-capex",
                    "regional-operacional"}


def _engine_sqlalchemy(pg_url):
    from sqlalchemy import create_engine
    return create_engine(pg_url)


def _e_tabela_ausente(e):
    """True SO para 'relation does not exist' (SQLSTATE 42P01). Qualquer outro erro
    (permissao negada, timeout, conexao caida) nao pode ser confundido com aba opcional."""
    return getattr(getattr(e, "orig", e), "pgcode", None) == "42P01" or "42P01" in str(e)


def snapshot_input_para_xlsx(pg_url, destino_xlsx, schema="input"):
    """Le as tabelas de input do Postgres e grava um .xlsx com os nomes de aba do motor.

    So as tabelas de `ABAS_OPCIONAIS` podem faltar, e SO com SQLSTATE 42P01. Qualquer
    outro erro estoura: uma falha de permissao/rede ao ler `metas_cobertura` produziria um
    Cenario sem metas, que resolve, passa no portao (as checagens de meta sao condicionais)
    e e publicado como SUCESSO.

    Retorna a lista de abas efetivamente gravadas.
    """
    import pandas as pd
    eng = _engine_sqlalchemy(pg_url)
    escritas = []
    try:
        with pd.ExcelWriter(destino_xlsx, engine="openpyxl") as xw:
            for aba, tabela in ABAS_INPUT.items():
                try:
                    df = pd.read_sql(f'SELECT * FROM "{schema}"."{tabela}"', eng)
                except Exception as e:
                    if aba in ABAS_OPCIONAIS and _e_tabela_ausente(e):
                        continue              # a unidade realmente nao tem essa aba
                    raise RuntimeError(
                        f"falha ao ler {schema}.{tabela} (aba '{aba}'): {e}") from e
                if df is None or len(df) == 0:
                    # Tabela VAZIA nao e o mesmo que ausente, e as consequencias diferem:
                    #  - estrutural vazia = Cenario incoerente, quase sempre carga
                    #    interrompida -> erro;
                    #  - as demais o motor tolera (fator_esgoto vazio cai em paridade 1.0;
                    #    metas_cobertura vazio = rodada so por VPL) -> AVISA, alto, porque
                    #    "sem metas" tambem e o sintoma de um TRUNCATE indevido.
                    if aba in ABAS_ESTRUTURAIS:
                        raise RuntimeError(
                            f"{schema}.{tabela} (aba '{aba}') existe mas esta VAZIA. Sem ela "
                            f"o Cenario fica incoerente — verifique a carga do cadastro.")
                    if aba not in ABAS_OPCIONAIS:
                        print(f"  [aviso] {schema}.{tabela} existe mas esta VAZIA: a rodada "
                              f"seguira sem esses dados. Se nao era esperado, confira a carga.")
                    continue
                # o Excel limita o nome da aba a 31 chars e o motor le pelo nome EXATO:
                # um nome mais longo seria truncado e a aba sumiria em silencio.
                if len(aba) > 31:
                    raise RuntimeError(f"nome de aba com mais de 31 chars: '{aba}' "
                                       f"(o Excel truncaria e o motor nao acharia)")
                df.to_excel(xw, sheet_name=aba, index=False)
                escritas.append(aba)
    finally:
        eng.dispose()                         # senao cada rodada deixa um pool aberto
    return escritas


def carregar_postgres(pg_url, schema="input", snapshot_para=None, **params):
    """Le o input do Postgres e devolve o Cenario (via ler_banco sobre um xlsx temporario).

    `params` sao os parametros da rodada (os mesmos da celula PARAMETROS do notebook):
    orcamento, unidade, base_receita, usar_cts, cobertura_so_residencial, foco_cobertura,
    penalidade_cobertura, anos_extra_conclusao, ete_faseada, etc.

    `snapshot_para`: caminho onde MANTER o xlsx materializado, em vez de apaga-lo. Ele e a
    copia congelada do cadastro daquela rodada — o job passa esse caminho para
    `materializar(arquivo_fonte=...)` e assim as tabelas snapshot__* e o banco_md5 existem.
    Sem isso a camada de reproducao/auditoria fica vazia: o arquivo era apagado aqui antes
    de qualquer um poder usa-lo. Quem pede e responsavel por apagar depois.
    """
    from otimizador.dominio import otimizador_capex_v62 as M   # o motor (mesmo do caminho Excel)
    if snapshot_para:
        destino, apagar = snapshot_para, False
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp.close()
        destino, apagar = tmp.name, True
    try:
        abas = snapshot_input_para_xlsx(pg_url, destino, schema=schema)
        if "subbacia-operacional" not in abas:
            raise RuntimeError("input incompleto no Postgres: falta 'subbacia_operacional'")
        return M.ler_banco(destino, **params)
    finally:
        if apagar:
            try:
                os.unlink(destino)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Validacao do MECANISMO sem Postgres: xlsx -> DataFrames -> xlsx -> ler_banco
# deve dar um Cenario identico ao ler_banco(xlsx original). Prova que a
# "materializacao em xlsx" nao altera nada. (roda no CI, offline)
# ---------------------------------------------------------------------------
def _roundtrip_xlsx(origem_xlsx, destino_xlsx):
    """Simula o Postgres: le cada aba do xlsx em DataFrame e regrava — mesmo caminho
    que `snapshot_input_para_xlsx`, mas a fonte e um xlsx em vez do banco."""
    import pandas as pd
    # `with` no ExcelFile: handle aberto trava o arquivo no Windows e quem tentar apaga-lo
    # depois leva PermissionError — foi o que aconteceu com o snapshot temporario do job.
    with pd.ExcelFile(origem_xlsx) as xls:
        abas = list(xls.sheet_names)
        with pd.ExcelWriter(destino_xlsx, engine="openpyxl") as xw:
            for aba in abas:
                xls.parse(aba).to_excel(xw, sheet_name=aba[:31], index=False)
    return destino_xlsx
