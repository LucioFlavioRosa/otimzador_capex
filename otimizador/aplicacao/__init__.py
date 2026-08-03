"""APLICACAO — casos de uso; orquestra dominio + infraestrutura, sem regra de negocio.

    job_databricks.py      UMA rodada de producao, fim a fim: le run_request -> RODANDO ->
                           carrega input -> resolve -> materializa -> PORTAO -> publica.
    experimentos_local.py  rodadas locais de desenvolvimento/analise (sem banco).
"""
