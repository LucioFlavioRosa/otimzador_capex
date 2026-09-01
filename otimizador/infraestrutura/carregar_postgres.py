"""Adaptador de dados: Postgres -> Cenario.

Le as tabelas de INPUT do Postgres (o cadastro que o front grava) e monta o dicionario
de ABAS que `ler_banco` consome. Nao existe arquivo no meio: o motor recebe os dados em
memoria, e nenhuma planilha e lida ou escrita em lugar nenhum deste caminho.

    from otimizador.infraestrutura.carregar_postgres import carregar_postgres
    cen = carregar_postgres(pg_url, schema="input", **params_da_rodada)

## Por que "aba", se nao ha planilha

"Aba" e o nome do RECORTE que o motor pede — `cidade-operacional`, `sistema-topologia` —,
e nao o de uma guia de Excel. O vocabulario ficou; o arquivo, nao. Trocar o nome exigiria
mexer nas 18 leituras do motor sem ganhar nada.

## A hierarquia v8, e por que a consulta nao e um `SELECT *`

O modelo do cadastro trocou a SUPERINTENDENCIA (um nivel de reserva que fonte nenhuma
trazia) pela EMPRESA OPERADORA, que e real: `input.empresa` e `input.cidade_empresa`
substituiram `regional_superintendencia` e `superintendencia_cidade`.

O motor continua falando `superintendencia_id` — e o elo entre cidade e unidade, e
renomea-lo atravessaria o nucleo inteiro sem mudar um resultado. Entao a traducao mora
AQUI, nas duas consultas com `AS`: a empresa entra como superintendencia, e o motor segue
igual. Quem for renomear o conceito no nucleo um dia mexe so nestas duas linhas.
"""
from __future__ import annotations

#: Aba (o recorte que o motor pede) -> consulta que a produz.
#:
#: A maioria e `SELECT * FROM <tabela>`. As duas da hierarquia sao projecoes da v8, e a
#: de `superintendencia-cidade` junta `cidade` para trazer o NOME — o motor usa
#: `cidade_name` no rotulo, e sem ele a cidade aparece pelo codigo.
ABAS_INPUT = {
    "unidade-regional":            "SELECT * FROM {s}.unidade_regional",
    "regional-superintendencia":   "SELECT emp_codigo AS superintendencia_id, unidade_id "
                                   "FROM {s}.empresa",
    "superintendencia-cidade":     "SELECT ce.cidade_id, ce.emp_codigo AS superintendencia_id, "
                                   "c.cidade_name FROM {s}.cidade_empresa ce "
                                   "LEFT JOIN {s}.cidade c ON c.cidade_id = ce.cidade_id",
    "cidade-sistema":              "SELECT * FROM {s}.cidade_sistema",
    "sistema-topologia":           "SELECT * FROM {s}.sistema_topologia",
    "cidade-operacional":          "SELECT * FROM {s}.cidade_operacional",
    "subbacia-operacional":        "SELECT * FROM {s}.subbacia_operacional",
    "componentes-subbacias-capex": "SELECT * FROM {s}.componentes_subbacias_capex",
    "ete-capex":                   "SELECT * FROM {s}.ete_capex",
    "regional-operacional":        "SELECT * FROM {s}.regional_operacional",
    "metas-cobertura":             "SELECT * FROM {s}.metas_cobertura",
    "fator-esgoto":                "SELECT * FROM {s}.fator_esgoto",
    # CTS (opcionais — so existem se a unidade tiver CTS)
    "subbacia-cts":                "SELECT * FROM {s}.subbacia_cts",
    "cts-operacional":             "SELECT * FROM {s}.cts_operacional",
    "componentes-cts-capex":       "SELECT * FROM {s}.componentes_cts_capex",
    # teto de CAPEX por regional/unidade — o motor le esta aba como FALLBACK quando
    # ORCAMENTO nao vem no run_request (v62: orc_reg). Sem ela e sem o parametro, o
    # motor usa INF e o plano sai sem teto.
    "orcamento":                   "SELECT * FROM {s}.orcamento",
}

#: Abas que podem legitimamente NAO existir (a unidade nao tem CTS; o orcamento veio por
#: parametro). So estas toleram SQLSTATE 42P01.
ABAS_OPCIONAIS = {"subbacia-cts", "cts-operacional", "componentes-cts-capex", "orcamento"}

#: Abas cujo conteudo VAZIO e sempre erro: sem elas o Cenario nao faz sentido. As que ficam
#: de fora podem vir vazias — o motor tolera (`fator_esgoto` sem faixas cai em paridade
#: 1.0; `metas_cobertura` vazio = rodada so por VPL; `ete_capex` vazio = sistema sem ETE) —
#: mas a carga avisa em voz alta.
ABAS_ESTRUTURAIS = {"unidade-regional", "regional-superintendencia", "superintendencia-cidade",
                    "cidade-sistema", "sistema-topologia", "cidade-operacional",
                    "subbacia-operacional", "componentes-subbacias-capex",
                    "regional-operacional"}


#: Aba -> DESTINOS na carga: `[(tabela, colunas_ou_None, {coluna_da_aba: coluna_da_tabela})]`.
#:
#: E o inverso de `ABAS_INPUT`, e existe separado porque os dois sentidos NAO sao
#: simetricos. Ler `regional-superintendencia` e uma projecao de `empresa`; escrever de
#: volta precisa saber que `superintendencia_id` vira `emp_codigo`. E a aba
#: `superintendencia-cidade` alimenta DUAS tabelas — o municipio passou a existir por si
#: (`cidade`) e o vinculo ficou em `cidade_empresa` —, o que um mapa de um-para-um nao
#: consegue dizer.
#:
#: `colunas=None` significa "todas as que a tabela tiver". A ORDEM da lista importa:
#: `cidade` antes de `cidade_empresa`, senao a FK reprova.
#:
#: Usado por `scripts/smoke_test_postgres.py` para semear um Postgres de teste.
TABELAS_DE_CARGA = {
    "unidade-regional":            [("unidade_regional", None, {})],
    "regional-superintendencia":   [("empresa", ["emp_codigo", "unidade_id"],
                                     {"superintendencia_id": "emp_codigo"})],
    "superintendencia-cidade":     [("cidade", ["cidade_id", "cidade_name"], {}),
                                    ("cidade_empresa", ["cidade_id", "emp_codigo"],
                                     {"superintendencia_id": "emp_codigo"})],
    "cidade-sistema":              [("cidade_sistema", None, {})],
    "sistema-topologia":           [("sistema_topologia", None, {})],
    "cidade-operacional":          [("cidade_operacional", None, {})],
    "subbacia-operacional":        [("subbacia_operacional", None, {})],
    "componentes-subbacias-capex": [("componentes_subbacias_capex", None, {})],
    "ete-capex":                   [("ete_capex", None, {})],
    "regional-operacional":        [("regional_operacional", None, {})],
    "metas-cobertura":             [("metas_cobertura", None, {})],
    "fator-esgoto":                [("fator_esgoto", None, {})],
    "subbacia-cts":                [("subbacia_cts", None, {})],
    "cts-operacional":             [("cts_operacional", None, {})],
    "componentes-cts-capex":       [("componentes_cts_capex", None, {})],
    "orcamento":                   [("orcamento", None, {})],
}


def _engine_sqlalchemy(pg_url):
    from sqlalchemy import create_engine
    return create_engine(pg_url)


def _e_tabela_ausente(e):
    """True SO para 'relation does not exist' (SQLSTATE 42P01). Qualquer outro erro
    (permissao negada, timeout, conexao caida) nao pode ser confundido com aba opcional."""
    return getattr(getattr(e, "orig", e), "pgcode", None) == "42P01" or "42P01" in str(e)


def abas_do_postgres(pg_url, schema="input", verbose=True):
    """Le o input do Postgres e devolve `{aba: [linha, ...]}` — o que `ler_banco` consome.

    Cada linha e um dicionario coluna -> valor, com a coluna como esta no banco (ja
    minuscula e com `_`), que e exatamente a normalizacao que o motor espera.

    SO as abas de `ABAS_OPCIONAIS` podem faltar, e SO com SQLSTATE 42P01. Qualquer outro
    erro estoura: uma falha de permissao ao ler `metas_cobertura` produziria um Cenario sem
    metas, que resolve, passa no portao (as checagens de meta sao condicionais) e e
    publicado como SUCESSO.

    NaN VIRA None. O `read_sql` do pandas devolve `NaN` para NULL em coluna numerica, e o
    motor testa `is None` — um NaN passa pelo teste e contamina a conta como float, sem
    erro em lugar nenhum.
    """
    import pandas as pd

    eng = _engine_sqlalchemy(pg_url)
    abas: dict[str, list[dict]] = {}
    for aba, sql in ABAS_INPUT.items():
        try:
            df = pd.read_sql(sql.format(s=schema), eng)
        except Exception as e:
            if aba in ABAS_OPCIONAIS and _e_tabela_ausente(e):
                continue                      # a unidade realmente nao tem essa aba
            raise RuntimeError(f"falha ao ler a aba '{aba}' de {schema}: {e}") from e

        if df is None or len(df) == 0:
            # Tabela VAZIA nao e o mesmo que ausente, e as consequencias diferem:
            #  - estrutural vazia = Cenario incoerente, quase sempre carga interrompida;
            #  - as demais o motor tolera, mas "sem metas" tambem e o sintoma de um
            #    TRUNCATE indevido -> avisa alto.
            if aba in ABAS_ESTRUTURAIS:
                raise RuntimeError(
                    f"a aba '{aba}' existe mas esta VAZIA. Sem ela o Cenario fica "
                    f"incoerente — verifique a carga do cadastro.")
            if aba not in ABAS_OPCIONAIS and verbose:
                print(f"  [aviso] a aba '{aba}' existe mas esta VAZIA: a rodada seguira "
                      f"sem esses dados. Se nao era esperado, confira a carga.")
            abas[aba] = []
            continue

        abas[aba] = [
            {k: (None if pd.isna(v) else v) for k, v in linha.items()}
            for linha in df.to_dict(orient="records")
        ]

    if verbose:
        print(f"  [info] input lido do Postgres: {len(abas)} aba(s), "
              f"{sum(len(v) for v in abas.values())} linha(s)")
    return abas


def carregar_postgres(pg_url, schema="input", **params):
    """Le o input do Postgres e devolve o Cenario. `params` vai inteiro para `ler_banco`."""
    from otimizador.dominio import otimizador_capex_v62 as M   # o motor
    return M.ler_banco(abas_do_postgres(pg_url, schema=schema), **params)
