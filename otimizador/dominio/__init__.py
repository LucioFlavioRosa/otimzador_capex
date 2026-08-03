"""DOMINIO — o coracao do sistema, puro (sem SQL, sem rede, sem credencial).

    otimizador_capex_v62.py      modelo economico, `ler_banco` (caminho Excel/dev),
                                 `avaliar`, VPL por sub-bacia. INTACTO na reorganizacao:
                                 o golden e a suite protegem cada numero.
    otimizador_capex_cpsat63.py  solver OR-Tools CP-SAT (geracao de colunas por cidade).
    qualidade.py                 portao de qualidade POR RODADA (14 checagens criticas),
                                 roda antes de publicar.
    contrato_resultado.py        linguagem ubiqua do RESULTADO: as 14 tabelas publicadas,
                                 suas chaves primarias e indices.

Um `import psycopg2`, um `open()` de rede ou um `requests` aqui dentro e um bug de
arquitetura, mesmo que funcione.
"""
