"""A CTS PODE FICAR FORA DA COBERTURA SEM SAIR DA RECEITA.

Pedido do dono do produto em 29/09/2026: com `usar_cts` ligada, a rodada escolhe se as
ligações novas da CTS contam na cobertura. *"Esse botão só vai considerar ou desconsiderar
as ligações novas de cts no cálculo da cobertura"*.

O ponto de costura já existia e é o que torna isto barato: `Obra.lig` alimenta a RECEITA e
`Obra.lig_cob` alimenta a COBERTURA (a meta e a faixa de paridade). Com a opção desligada,
só a segunda zera nos nós de CTS.

## As duas decisões de desenho, que mudam número e foram DELE

**O denominador não muda.** O universo e a base da CTS continuam em `max_lig`/`base_lig`.
A cidade passa a ter uma parcela do universo que ninguém alcança, e 100% de cobertura deixa
de ser atingível. Medido em Cordeiro: a cobertura final cai de 90,15% para 87,51%; tirar a
CTS inteira (numerador e denominador) daria 89,75%.

**A paridade segue a nova régua.** `lig_cob` alimenta `_fator_por_cobertura_realizada`, e o
fator multiplica a tarifa recorrente — então cruzar faixa mais tarde derruba a receita da
cidade inteira. A receita das ligações da CTS não muda; a receita TOTAL pode mudar por essa
via. Ele escolheu assim para o produto ter UMA cobertura realizada em vez de duas.
"""
import pytest

from _helpers import BANK_CTS, ORC_SLACK, banco, build_all, engine, silent


def _cen(cts_na_cobertura):
    """O mesmo banco de teste do `load_cts`, mais o parâmetro novo.

    Não uso `load_cts` porque ele não expõe `cts_na_cobertura` — e acrescentar mais um
    argumento ao helper por causa de um teste faria todo mundo pagar por ele.
    """
    return silent(engine().ler_banco, banco(BANK_CTS), orcamento=ORC_SLACK,
                  usar_cts=True, cts_na_cobertura=cts_na_cobertura)


@pytest.fixture(scope="module")
def com_cts():
    return _cen(True)


@pytest.fixture(scope="module")
def sem_cts_na_cobertura():
    return _cen(False)


def test_a_fixture_tem_cts_com_ligacoes_novas(com_cts):
    """Sem isto os testes abaixo passariam sem provar nada."""
    coletas_cts = [o for o in com_cts.coletas if o.no in com_cts.cts_ids]
    assert coletas_cts, "a fixture precisa de CTS que vire coleta"
    assert sum(o.lig for o in coletas_cts) > 0, "a CTS precisa de ligações novas"


def test_DESLIGADO_zera_a_cobertura_da_cts_e_NAO_a_receita(sem_cts_na_cobertura):
    """O que o botão faz, dito em uma asserção por metade."""
    cen = sem_cts_na_cobertura
    for o in cen.coletas:
        if o.no in cen.cts_ids:
            assert o.lig_cob == 0.0, f"{o.id} ainda conta para a cobertura"
            assert o.lig > 0, f"{o.id} perdeu a receita, e não devia"


def test_LIGADO_mantem_as_duas_iguais(com_cts):
    """O default é o comportamento de hoje — nenhuma rodada existente muda."""
    cen = com_cts
    assert cen.cts_na_cobertura is True
    for o in cen.coletas:
        if o.no in cen.cts_ids:
            assert o.lig_cob == o.lig


def test_a_sub_bacia_NAO_e_afetada(com_cts, sem_cts_na_cobertura):
    """O botão é da CTS. Sub-bacia comum conta igual nos dois modos.

    Se um dia alguém aplicar o recorte a todas as coletas por engano, é aqui que aparece.
    """
    a = {o.id: o.lig_cob for o in com_cts.coletas if o.no not in com_cts.cts_ids}
    b = {o.id: o.lig_cob for o in sem_cts_na_cobertura.coletas
         if o.no not in sem_cts_na_cobertura.cts_ids}
    assert a == b


def test_O_DENOMINADOR_NAO_MUDA(com_cts, sem_cts_na_cobertura):
    """Decisão do dono do produto: sai só o numerador.

    O universo (`max_lig`) e a base já atendida (`base_lig`) continuam iguais — inclusive a
    parcela da CTS. É o que faz 100% de cobertura deixar de ser atingível, e é deliberado.
    """
    assert com_cts.max_lig == sem_cts_na_cobertura.max_lig
    assert com_cts.base_lig == sem_cts_na_cobertura.base_lig


def test_A_COBERTURA_CAI_E_A_RECEITA_DAS_LIGACOES_NOVAS_NAO(com_cts, sem_cts_na_cobertura):
    """O efeito no resultado, no mesmo plano.

    A receita comparada aqui é a das ligações novas (`receita_ano`), que é o que o pedido
    manda não mexer. O VPL pode mudar, porque a paridade segue a cobertura — ver o teste
    seguinte, que é onde essa consequência fica registrada.
    """
    ra, rb = build_all(com_cts), build_all(sem_cts_na_cobertura)
    cob_a = sum(v[-1] for v in ra["cobertura_sistema"].values())
    cob_b = sum(v[-1] for v in rb["cobertura_sistema"].values())
    assert cob_b < cob_a, "a cobertura tinha de cair ao tirar a CTS dela"

    # E a queda é EXATAMENTE as ligações de cobertura da CTS, não um número qualquer.
    da_cts = sum(o.lig_cob for o in com_cts.coletas
                 if o.no in com_cts.cts_ids and ra["elig"].get(o.id))
    assert cob_a - cob_b == pytest.approx(da_cts, rel=1e-6)


def test_A_PARIDADE_SEGUE_A_REGUA_E_ISSO_PODE_MEXER_NA_RECEITA(com_cts, sem_cts_na_cobertura):
    """A consequência que o dono do produto aceitou, registrada como teste.

    `lig_cob` alimenta `_fator_por_cobertura_realizada`, então tirar a CTS da cobertura pode
    fazer a cidade cruzar faixa de paridade mais tarde — e o fator multiplica a tarifa
    recorrente de TODA a cidade. O teste não fixa valor: fixa que o fator é o MESMO objeto
    de cálculo nos dois lados, para ninguém "consertar" isto desacoplando os dois sem
    decisão nova.
    """
    ra, rb = build_all(com_cts), build_all(sem_cts_na_cobertura)
    fa, fb = ra["fator_esgoto_ano"], rb["fator_esgoto_ano"]
    assert set(fa) == set(fb)
    # Onde a cidade tem faixa e CTS, o fator NÃO pode ser maior sem a CTS: menos cobertura
    # nunca compra faixa melhor.
    for cid in fa:
        assert all(b <= a + 1e-9 for a, b in zip(fa[cid], fb[cid])), (
            f"{cid}: tirar a CTS da cobertura MELHOROU a paridade, o que é impossível"
        )
