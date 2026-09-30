"""O JOB FALHA CEDO QUANDO O SCHEMA DE RESULTADO ESTÁ DESATUALIZADO.

Achado pela revisão de produção do Codex, em 30/09/2026, com a mudança já pronta para subir
na Azure. Ele mediu o que acontece quando o motor novo roda contra um banco sem a migração
02 do schema de resultado:

    EXC_TYPE= UndefinedColumn
    EXC_MSG= column "capex_terreno" of relation "otim_obra" does not exist
    meta_rows_after_failure= 0
    obra_rows_after_failure= 0

A publicação é atômica — não sobra `otim_meta` nem `otim_obra` —, mas a falha acontece
DEPOIS de a rodada carregar o cadastro, resolver, materializar e passar pelo portão de
qualidade. O custo é uma execução inteira do Databricks, e o blob pode já ter sido escrito
(a ordem do job é blob → Postgres → notificação). E a mensagem que o operador recebe não diz
qual migração aplicar.

Em produção isso pesa mais: acesso só por VPN, deploy do motor separado do serviço, e
migrações aplicadas à mão. O `/readyz` do serviço já recusa o pod quando falta migração de
`input`; o job não tinha nada equivalente.

Agora `_exigir_colunas_do_resultado` roda ANTES do solver, no mesmo lugar e no mesmo estilo
de `_exigir_teto_anual`.
"""
import os

import pytest

from otimizador.aplicacao import job_databricks as J
from otimizador.infraestrutura import persistencia as P

URL = os.environ.get("OTIMIZADOR_PG_TESTE", "")


# ----------------------------------------------------------------- sem banco
def test_a_lista_do_preflight_cobre_o_que_a_publicacao_GRAVA():
    """O guarda que impede a próxima coluna de escapar.

    Se a publicação passar a gravar outra coluna vinda de migração e ela não entrar na
    lista, a falta volta a aparecer no fim da rodada. A conferência é contra o código da
    publicação, e não contra uma lista escrita duas vezes.
    """
    import inspect
    fonte = inspect.getsource(P._tabela_obra) if hasattr(P, "_tabela_obra") else ""
    if not fonte:
        import pathlib
        fonte = pathlib.Path(P.__file__).read_text(encoding="utf-8")
    no_preflight = {c for c, _a in J._COLUNAS_DE_MIGRACAO_DO_RESULTADO}
    for coluna in ("capex_terreno", "capex_modulos_iniciais", "capex_modulos_expansao"):
        assert f'"{coluna}"' in fonte, f"a publicação deveria gravar {coluna}"
        assert coluna in no_preflight, f"{coluna} é gravada e não está no preflight"


def test_a_mensagem_diz_QUAL_MIGRACAO_aplicar():
    """Quem lê "falta ddl_resultado_migracao_02.sql" resolve em um minuto; quem lê
    "UndefinedColumn" abre investigação."""
    for _coluna, arquivo in J._COLUNAS_DE_MIGRACAO_DO_RESULTADO:
        assert arquivo.endswith(".sql") and "migracao" in arquivo, arquivo


# ----------------------------------------------------------------- com banco
def _sem_banco():
    return not URL.endswith("/otimizador")


@pytest.mark.skipif(_sem_banco(), reason="defina OTIMIZADOR_PG_TESTE com a URL de um Postgres")
def test_o_schema_ATUALIZADO_passa():
    J._exigir_colunas_do_resultado(URL, schema="public")


@pytest.mark.skipif(_sem_banco(), reason="defina OTIMIZADOR_PG_TESTE com a URL de um Postgres")
def test_o_schema_VELHO_e_recusado_ANTES_do_solver():
    """Um `otim_obra` sem as três colunas, num schema temporário, como era antes da
    migração. O preflight tem de recusar nomeando o arquivo."""
    from sqlalchemy import create_engine, text
    eng = create_engine(URL)
    try:
        with eng.begin() as con:
            con.execute(text("DROP SCHEMA IF EXISTS otim_preflight_teste CASCADE"))
            con.execute(text("CREATE SCHEMA otim_preflight_teste"))
            con.execute(text("CREATE TABLE otim_preflight_teste.otim_obra ("
                             " run_id text, obra_id text, capex double precision)"))
        with pytest.raises(RuntimeError) as e:
            J._exigir_colunas_do_resultado(URL, schema="otim_preflight_teste")
        assert "ddl_resultado_migracao_02.sql" in str(e.value)
        assert "capex_terreno" in str(e.value)
    finally:
        with eng.begin() as con:
            con.execute(text("DROP SCHEMA IF EXISTS otim_preflight_teste CASCADE"))
        eng.dispose()


@pytest.mark.skipif(_sem_banco(), reason="defina OTIMIZADOR_PG_TESTE com a URL de um Postgres")
def test_schema_INEXISTENTE_nao_e_erro():
    """`publicar_postgres(criar=True)` cria o schema do zero, e ali a tabela nasce com as
    colunas. O preflight cobra a tabela que EXISTE e está velha, não a que não existe."""
    J._exigir_colunas_do_resultado(URL, schema="otim_que_nao_existe")
