"""Configuracao do pytest: registra marcadores e disponibiliza cenarios reutilizaveis."""
import pytest
from _helpers import load_cts, build_all


def pytest_configure(config):
    config.addinivalue_line("markers", "solver: requer OR-Tools (pula se ausente)")
    config.addinivalue_line("markers", "slow: teste lento (decomposicao por cidade)")


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
