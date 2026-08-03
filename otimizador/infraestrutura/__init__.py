"""INFRAESTRUTURA — adaptadores de I/O. E a UNICA camada que fala com o mundo externo.

    carregar_postgres.py  Postgres (schema `input`) -> Cenario, reusando `ler_banco`.
    persistencia.py       cen + res -> 14 tabelas run_* (+ snapshots); parquet/Delta.
    publicacao.py         escrita transacional no Postgres, status, diagnostico,
                          blob e notificacao (Service Bus / webhook).
    sql/                  DDLs de input/controle e de resultado (migrations).
"""
