"""UM SISTEMA PODE ESTAR EM MAIS DE UMA CIDADE, e o no e da cidade DELE.

A regra do cliente mudou (09/2026): o sistema de esgotamento atravessa municipio.
No cadastro, `cidade_sistema` passou a ter uma linha por cidade do sistema, e a
sub-bacia ganhou `cidade_id` (migracao 022). Aqui, `ler_banco` reduzia a tabela a
um mapa sistema -> UMA cidade, a ultima que viesse — e todo no de um sistema em
duas cidades recebia a sobrevivente. Cobertura por cidade, metas e fator de esgoto
iam para a cidade errada, e a cidade "apagada" nem entrava no aviso de meta
ausente.

O caso e montado sobre a fixture: `s1` passa a estar em `c1` E `c2`, e `b2`
declara `c2`. Antes do conserto, `b2` era de `c1` (ou de `c2` — dependia da ordem
das linhas). Cada teste derruba um pedaco do conserto.
"""
import copy

import pytest
from _helpers import banco, BANK_FIXTURE, engine, silent


def _fixture_multicidade(ordem_invertida=False):
    """s1 em c1 e c2; b1 fica em c1, b2 declara c2. `ordem_invertida` troca a ordem
    das linhas de `cidade-sistema` — o defeito antigo dependia dela."""
    b = banco(BANK_FIXTURE)
    linha_c2 = {"cidade_id": "c2", "sistema_id": "s1", "sistema_name": "Sistema A"}
    b["cidade-sistema"] = (
        [linha_c2] + b["cidade-sistema"] if ordem_invertida else b["cidade-sistema"] + [linha_c2]
    )
    for r in b["subbacia-operacional"]:
        r["cidade_id"] = {"b1": "c1", "b2": "c2", "b3": "c2", "b4": "c2"}[r["sub_bacia"]]
    return b


def _ler(b):
    return silent(engine().ler_banco, b, unidade="u1", usar_cts=False)


@pytest.mark.parametrize("ordem_invertida", [False, True])
def test_o_no_e_da_cidade_DELE_e_nao_da_ultima_linha_do_sistema(ordem_invertida):
    cen = _ler(_fixture_multicidade(ordem_invertida))
    # O mesmo sistema, duas cidades — e cada no na sua.
    assert cen.nos["b1"].cidade == "Cidade A"
    assert cen.nos["b2"].cidade == "Cidade B"
    assert cen.nos["b1"].sistema == cen.nos["b2"].sistema == "Sistema A"


def test_sem_cidade_propria_o_no_cai_na_do_sistema():
    """Base anterior a migracao 022, ou carga sem a coluna: o comportamento de antes."""
    b = _fixture_multicidade()
    for r in b["subbacia-operacional"]:
        r.pop("cidade_id", None)
    cen = _ler(b)
    # A reserva e a MENOR cidade do sistema, em ordem — a mesma toda vez.
    assert cen.nos["b1"].cidade == "Cidade A"
    assert cen.nos["b2"].cidade == "Cidade A"


def test_a_cobertura_por_cidade_conta_a_sub_bacia_na_cidade_dela():
    """`b2` esta em `c2`: a cobertura de `Cidade B` tem de incluir o universo de b2.

    Era o estrago mais caro do defeito: a cidade sobrevivente recebia ligacoes
    demais, e a outra ficava sem as dela — com meta e fator aplicados ao lugar
    errado.
    """
    b = _fixture_multicidade()
    cen = _ler(b)
    universo_b2 = next(r["universo_ligacoes"] for r in b["subbacia-operacional"] if r["sub_bacia"] == "b2")
    por_cidade = {}
    for n in cen.nos.values():
        por_cidade.setdefault(n.cidade, []).append(n.id)
    assert "b2" in por_cidade["Cidade B"]
    assert "b2" not in por_cidade["Cidade A"]
    assert universo_b2 > 0


def test_sistema_em_uma_cidade_so_nao_muda_nada():
    """A fixture original: uma cidade por sistema. Todo numero igual ao de antes —
    e o golden (`test_regressao_golden`) e quem prende isso de verdade."""
    cen = _ler(banco(BANK_FIXTURE))
    assert {n.id: n.cidade for n in cen.nos.values()} == {
        "b1": "Cidade A", "b2": "Cidade A", "b3": "Cidade B", "b4": "Cidade B",
    }


def test_o_horizonte_e_o_da_concessao_que_acaba_primeiro():
    """Sistema em duas cidades com concessoes diferentes: vale a mais curta."""
    b = _fixture_multicidade()
    for r in b["cidade-operacional"]:
        r["data_fim_concessao"] = {"c1": 2045, "c2": 2035}[r["cidade_id"]]
    cen = _ler(b)
    so_c1 = copy.deepcopy(b)
    so_c1["cidade-sistema"] = [r for r in so_c1["cidade-sistema"] if not (r["sistema_id"] == "s1" and r["cidade_id"] == "c2")]
    cen_c1 = _ler(so_c1)
    # O horizonte e por SISTEMA (`Cenario.hz`): 2035 acaba antes de 2045.
    assert cen.hz["Sistema A"] < cen_c1.hz["Sistema A"]
    assert cen.hz["Sistema A"] == 2035 - 2026 + 1
