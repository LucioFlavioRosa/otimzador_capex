"""Otimizador de CAPEX — pacote organizado por camadas (Domain-Driven Design).

    dominio/         o motor puro (modelo economico + solver) e o portao de qualidade.
                     Zero I/O de banco/rede — e o que mantem os 83 testes possiveis.
    aplicacao/       casos de uso que ORQUESTRAM o dominio: o job de producao
                     (Databricks) e os experimentos locais. Nada de regra de negocio aqui.
    infraestrutura/  adaptadores de I/O: leitura do Postgres -> Cenario, materializacao
                     em tabelas, publicacao transacional, DDLs (sql/).
    apresentacao/    o lado de consumo: contrato de leitura das telas (leitor_v2) e
                     explicabilidade/figuras (dashboard).

Regra de dependencia (de fora para dentro):
    apresentacao / infraestrutura / aplicacao  --->  dominio
    dominio NAO importa das outras camadas.

Entrada unica: `main.py` na raiz do projeto.
"""
