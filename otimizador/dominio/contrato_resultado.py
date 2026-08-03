"""Contrato do RESULTADO publicado — linguagem ubiqua entre dominio, publicacao e portao.

As 14 tabelas `run_*` (publicadas como `otim_*`), suas chaves primarias e os indices que o
front consulta. Mora no DOMINIO porque tanto o portao de qualidade (checagem de duplicatas
de PK) quanto a publicacao dependem dele — e dominio nao pode importar infraestrutura.
`publicacao.py` reexporta estes nomes, entao codigo existente continua funcionando.
"""

# tabelas que vao para o Postgres (as snapshot__* ficam so no blob)
TABELAS_SERVICO = [
    "run_meta", "run_obra", "run_subbacia", "run_subbacia_ano", "run_sistema",
    "run_dependencia",
    "run_ano", "run_mes",
    "run_cidade", "run_cidade_ano", "run_cobertura", "run_meta_cobertura",
    "run_paridade", "run_auditoria",
]
PREFIXO = "otim_"

# chaves e indices pensados no que o front consulta
CHAVES = {
    "run_meta":          ("run_id",),
    "run_obra":          ("run_id", "obra_id"),
    "run_subbacia":      ("run_id", "sub_bacia"),
    "run_subbacia_ano":  ("run_id", "sub_bacia", "ano"),
    "run_sistema":       ("run_id", "sistema"),
    "run_dependencia":   ("run_id", "obra_id", "sub_bacia"),
    "run_ano":           ("run_id", "ano"),
    "run_mes":           ("run_id", "mes_indice"),
    "run_cidade":        ("run_id", "cidade"),
    "run_cidade_ano":    ("run_id", "cidade", "ano"),
    "run_cobertura":     ("run_id", "cidade", "ano"),
    "run_meta_cobertura": ("run_id", "cidade", "ano"),
    "run_paridade":      ("run_id", "cidade", "ano"),
    "run_auditoria":     None,                       # sem PK natural (violacoes + reparos)
}
INDICES = {
    "run_obra":        [("run_id", "cidade"), ("run_id", "status"), ("run_id", "categoria_motivo")],
    "run_subbacia":    [("run_id", "cidade"), ("run_id", "faturando")],
    "run_dependencia": [("run_id", "sub_bacia"), ("run_id", "obra_id")],
    "run_subbacia_ano": [("run_id", "sub_bacia"), ("run_id", "cidade", "ano")],
    "run_cobertura":   [("run_id", "cidade")],
    "run_sistema":     [("run_id", "cidade")],
    "run_mes":         [("run_id", "ano")],
}
