"""Configuracao do pytest: registra marcadores e disponibiliza cenarios reutilizaveis."""
from datetime import date

import pytest
from _helpers import load_cts, build_all, engine


def pytest_configure(config):
    config.addinivalue_line("markers", "solver: requer OR-Tools (pula se ausente)")
    config.addinivalue_line("markers", "slow: teste lento (decomposicao por cidade)")


#: O "HOJE" DOS TESTES: um dia ANTERIOR a todo cronograma das fixtures (que comecam em
#: 2026). Sem `data_inicio`, o motor deriva a data de inicio das obras do primeiro ano
#: do CAPEX e do dia da rodada (`data_inicio_automatica`); com o dia fixado antes do
#: CAPEX, ela cai em janeiro do primeiro ano — o comportamento que o golden prende.
#: Deixar `date.today()` faria os numeros mudarem a cada mes do calendario.
HOJE_DOS_TESTES = date(2025, 1, 1)


@pytest.fixture(scope="session", autouse=True)
def hoje_fixo():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine(), "hoje", lambda: HOJE_DOS_TESTES)
        yield HOJE_DOS_TESTES


# cenarios do banco de teste CTS, carregados uma vez por sessao (Python puro, sem solver)
@pytest.fixture(scope="session")
def cen_on():
    return load_cts(True)


@pytest.fixture(scope="session")
def cen_off():
    return load_cts(False)


@pytest.fixture(scope="session")
def res_on(cen_on):
    return build_all(cen_on)


@pytest.fixture(scope="session")
def res_off(cen_off):
    return build_all(cen_off)
