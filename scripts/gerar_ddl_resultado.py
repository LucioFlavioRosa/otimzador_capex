"""Gera `ddl_resultado.sql` — o DDL das tabelas public.otim_* (o que o job publica).

    python main.py gerar-ddl

Por que existe um script em vez de uma linha de codigo:

`publicacao.ddl_postgres(tabs)` INFERE o tipo de cada coluna a partir do dtype do
DataFrame materializado. Coluna que esta TODA NULA na rodada usada como amostra nao tem
dtype util e cai em TEXT — foi o que aconteceu gerando so do banco de teste CTS:
`foco_cobertura`, `obrig_total`, `pot_vp_receita`, `capex_modulo` e outras sairam TEXT
sendo numericas. Uma coluna TEXT onde o job grava float nao quebra o INSERT (o Postgres
converte), mas quebra `ORDER BY`, `SUM`, comparacao e qualquer grafico do front.

A solucao aqui e materializar as TRES fixtures (que cobrem cenarios diferentes: com CTS,
sem CTS com mix de WACC, e com parcela industrial + reguas de cobertura distintas) e, para
cada coluna, ficar com o tipo MAIS ESPECIFICO que qualquer uma delas revelou. TEXT so
permanece se nenhuma rodada produziu valor numerico/booleano ali.

Se um dia sobrar coluna TEXT que voce sabe ser numerica, o lugar de corrigir e o dicionario
`publicacao.TIPOS_FIXOS` — nao este script, e nunca o .sql a mao.
"""
import io
import contextlib
import os
import sys

import matplotlib
matplotlib.use("Agg")                    # o dashboard importa pyplot; sem backend grafico

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # raiz do repo
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd                                             # noqa: E402
from otimizador.apresentacao import dashboard_otimizador_v2 as D   # noqa: E402
from otimizador.dominio import otimizador_capex_v62 as M            # noqa: E402
from otimizador.infraestrutura import persistencia as P             # noqa: E402
from otimizador.infraestrutura import publicacao as PUB             # noqa: E402

FIXTURES = [
    ("tests/fixtures/banco_teste_CTS_poc_v2.xlsx", dict(usar_cts=True)),
    ("tests/fixtures/banco_teste_CTS_poc_v2.xlsx", dict(usar_cts=True, ete_faseada=True)),
    ("tests/fixtures/banco_fixture_testes.xlsx",   dict(unidade="u1")),
    # Os dois lados do recorte da meta: a fixture `classe` tem colunas residenciais, e
    # rodar os dois garante que o DDL gerado cobre as colunas que cada modo publica.
    ("tests/fixtures/banco_fixture_classe.xlsx",   dict(cobertura_so_residencial=False)),
    ("tests/fixtures/banco_fixture_classe.xlsx",   dict(cobertura_so_residencial=True)),
]

CABECALHO = """-- ============================================================================
-- DDL — RESULTADO (public.otim_*)  |  Postgres/Azure
--
-- GERADO por `python main.py gerar-ddl`. Reflete exatamente o que
-- `publicacao.publicar_postgres` escreve. NAO edite a mao: um esquema divergente do
-- gerado faz o INSERT falhar com erro obscuro, ou aceita numero em coluna TEXT e quebra
-- ORDER BY/SUM no front.
--
-- Aplique como MIGRATION, uma vez. O job publica com `criar_schema=False` — DDL nao roda
-- no caminho quente (tomaria lock, e `CREATE TABLE IF NOT EXISTS` nao adiciona coluna
-- nova numa tabela que ja existe).
--
-- 14 tabelas + 3 views. Toda tabela de detalhe tem FK para otim_meta com ON DELETE
-- CASCADE: e o que faz a republicacao de um run_id ficar limpa (apaga e regrava).
--
-- Dicionario de colunas: docs/06-dicionario-resultado.md
-- ============================================================================
"""


def _materializar(caminho, kwargs):
    """Uma rodada build-all (deterministica, sem solver) -> dict de DataFrames."""
    with contextlib.redirect_stdout(io.StringIO()):
        cen = M.ler_banco(os.path.join(ROOT, caminho), orcamento=1e12, **kwargs)
        plano = {oid: max(0, int(o.inicio_min)) for oid, o in cen.obras.items() if o.eh_aegea()}
        return P.materializar(cen, M.avaliar(cen, plano), run_id="ddl", banco="ddl")


def tabelas_com_tipos_mesclados():
    """Materializa todas as fixtures e devolve um `tabs` em que cada coluna carrega o dtype
    MAIS INFORMATIVO visto em qualquer uma delas (object perde para numerico/bool)."""
    D.set_engine(M)
    P.set_engine(M, D)
    rodadas = [_materializar(c, kw) for c, kw in FIXTURES]

    mesclado = {}
    for nome in PUB.TABELAS_SERVICO:
        candidatos = [r[nome] for r in rodadas if r.get(nome) is not None]
        if not candidatos:
            continue
        base = max(candidatos, key=len)                  # a rodada com mais linhas manda
        colunas = {}
        for c in base.columns:
            serie = base[c]
            if serie.dtype.kind == "O":                  # object: procura tipo melhor
                for outro in candidatos:
                    if c in outro.columns and outro[c].dtype.kind != "O":
                        serie = outro[c]
                        break
            colunas[c] = serie
        mesclado[nome] = pd.DataFrame({c: s.reset_index(drop=True) for c, s in colunas.items()})
    return mesclado


def main():
    tabs = tabelas_com_tipos_mesclados()
    ddl = PUB.ddl_postgres(tabs, schema="public")
    # o gerador carimba data/hora no topo; isso nao versiona bem (diff a cada execucao)
    corpo = "\n".join(l for l in ddl.splitlines() if not l.startswith("-- DDL do otimizador"))
    destino = os.path.join(ROOT, "otimizador", "infraestrutura", "sql", "ddl_resultado.sql")
    with open(destino, "w", encoding="utf-8") as f:
        f.write(CABECALHO + corpo.lstrip("\n") + "\n")

    textuais = [(n, c) for n, df in tabs.items() for c in df.columns
                if PUB._tipo_pg(df[c], c) == "TEXT" and df[c].isna().all()]
    print(f"{destino}: {len(open(destino, encoding='utf-8').readlines())} linhas, "
          f"{len(tabs)} tabelas")
    if textuais:
        print("\nAVISO — colunas TEXT que ficaram TODAS NULAS em todas as fixtures "
              "(o tipo e um chute; confira e, se for numerica, declare em "
              "publicacao.TIPOS_FIXOS):")
        for n, c in textuais:
            print(f"  otim_{n[4:]}.{c}")


if __name__ == "__main__":
    main()
