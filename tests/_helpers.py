"""Utilidades compartilhadas pelos testes. NAO importa OR-Tools (o engine e Python puro);
o solver so e carregado sob demanda em `solver_or_skip`."""
import os
import sys
import io
import contextlib

# raiz do pacote = pasta acima de tests/ (onde ficam os .py e os bancos)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# A suite e AUTOSSUFICIENTE: le so de tests/fixtures/, e sem subir banco nenhum.
#
# AS FIXTURES SAO JSON, e nao planilha. O motor recebe as ABAS ja em dicionario
# (`ler_banco(abas)`), entao a fixture e exatamente esse dicionario, serializado. Ganha-se
# o que um .xlsx nao da num repositorio: o diff de uma fixture e legivel, e mudar uma
# celula aparece na revisao em vez de virar um blob binario.
#
# `banco(...)` devolve uma COPIA PROFUNDA a cada chamada. As fixtures sao lidas uma vez e
# ficam em cache, e mais de um teste altera o dicionario para montar seu cenario — sem a
# copia, o primeiro que escreve contamina todos os seguintes, e a ordem dos testes passa a
# decidir o resultado.
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
BANK_CTS = os.path.join(FIXTURES, "banco_teste_CTS_poc_v2.json")     # com CTS (2 coletores)
BANK_FIXTURE = os.path.join(FIXTURES, "banco_fixture_testes.json")   # SEM CTS + mix de WACC
BANK_CLASSE = os.path.join(FIXTURES, "banco_fixture_classe.json")    # industrial em b1/b3; c1=economias, c2=populacao
UNIDADE_FIXTURE = "u1"

_CACHE: dict = {}


def banco(caminho):
    """As abas da fixture, como `ler_banco` as espera — copia nova a cada chamada."""
    import copy
    import json
    if caminho not in _CACHE:
        with open(caminho, encoding="utf-8") as f:
            _CACHE[caminho] = json.load(f)
    return copy.deepcopy(_CACHE[caminho])

# orcamento FOLGADO: garante que o solver constroi tudo no inicio => solver == build-all
ORC_SLACK = {2026: 50e6, 2027: 50e6, 2028: 50e6, 2029: 50e6}


def silent(fn, *a, **k):
    """Executa silenciando o stdout (o engine imprime diagnosticos na carga)."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def require_bank(path):
    """Pula o teste (em vez de falhar) se o banco nao foi enviado para a sessao."""
    import pytest
    if not os.path.exists(path):
        pytest.skip(f"banco ausente nesta sessao: {os.path.basename(path)} "
                    f"(suba-o na mesma pasta para rodar este teste)")


def engine():
    from otimizador.dominio import otimizador_capex_v62 as M
    return M


def solver_or_skip():
    """Retorna o modulo do solver; pula o teste se OR-Tools nao estiver instalado.
    Tambem instala os shims de nome que a suite legada (testes_otimizador) espera."""
    import pytest
    try:
        import ortools  # noqa: F401
        from otimizador.dominio import otimizador_capex_v62 as M
        from otimizador.dominio import otimizador_capex_cpsat63 as CP
    except Exception as e:  # pragma: no cover - depende do ambiente
        pytest.skip(f"OR-Tools indisponivel: {e}")
    # compat com testes_otimizador.py, que importa os nomes antigos
    sys.modules.setdefault("otimizador_capex_v24", M)
    sys.modules.setdefault("otimizador_capex_cpsat24", CP)
    return CP


def load_cts(usar_cts, orc=None):
    M = engine()
    return silent(M.ler_banco, banco(BANK_CTS), orcamento=orc or ORC_SLACK, usar_cts=usar_cts)


def load_unidade(fonte, unidade, usar_cts=True):
    M = engine()
    return silent(M.ler_banco, banco(fonte) if isinstance(fonte, str) else fonte,
                  unidade=unidade, usar_cts=usar_cts)


def load_fixture(usar_cts=True, unidade=UNIDADE_FIXTURE, cobertura_so_residencial=False):
    """Banco fixo SEM CTS (com mix de WACC) que acompanha a suite — sempre presente."""
    M = engine()
    return silent(M.ler_banco, banco(BANK_FIXTURE), unidade=unidade, usar_cts=usar_cts,
                  cobertura_so_residencial=cobertura_so_residencial)


def load_classe(cobertura_so_residencial=False, unidade_cobertura="ligacoes"):
    """Banco com parcela industrial (b1/b3), lido na REGUA que o teste pedir.

    A regua era coluna da cidade — a fixture trazia c1 em economias e c2 em
    populacao, e cada teste herdava a sua sem dizer qual era. Virou PARAMETRO DE
    RODADA, e uma rodada tem UMA regua para a unidade inteira: cada teste agora
    declara em que moeda esta medindo, que e o que ele sempre quis dizer.
    """
    M = engine()
    return silent(M.ler_banco, banco(BANK_CLASSE),
                  cobertura_so_residencial=cobertura_so_residencial,
                  unidade_cobertura=unidade_cobertura)


def build_all(cen):
    """Plano 'constroi tudo no inicio_min' + avaliar (Python puro, sem solver).
    Como avaliar ignora o teto, os numeros economicos (VPL/CAPEX/cobertura) sao
    determinISticos e independem do orcamento."""
    M = engine()
    plano = {oid: max(0, int(o.inicio_min)) for oid, o in cen.obras.items() if o.eh_aegea()}
    return silent(M.avaliar, cen, plano)


def capex_total(cen, res):
    reg = list(cen.regionais)[0]
    return sum(res["capex_ano"][reg])


def cobertura_fim(res):
    return sum(v[-1] for v in res.get("cobertura_sistema", {}).values())


def codigo(obra_id):
    return str(obra_id).split("_")[0].lower()
