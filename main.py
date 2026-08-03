"""Ponto de entrada UNICO do Otimizador de CAPEX.

Recebe os inputs e orquestra, delegando para a camada de aplicacao — nenhuma regra de
negocio vive aqui. Comandos:

    python main.py rodar --run-id RUN --pg postgresql://...   # rodada de PRODUCAO
    python main.py experimento [...]                          # rodada local, sem banco
    python main.py smoke --pg postgresql://...                # valida o pipeline num Postgres
    python main.py gerar-ddl                                  # regenera o DDL de resultado

`python main.py <comando> --help` mostra as opcoes de cada um. Estrutura do pacote em
`otimizador/__init__.py` (dominio / aplicacao / infraestrutura / apresentacao).

No Databricks o entrypoint continua sendo a funcao, nao o CLI:

    from otimizador.aplicacao.job_databricks import rodar
    rodar(run_id=dbutils.widgets.get("run_id"),
          pg_url=dbutils.secrets.get("otimizador", "pg_url"), ...)
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:                     # permite `python main.py` de qualquer cwd
    sys.path.insert(0, ROOT)


def _cmd_rodar(argv):
    """Rodada de producao: o mesmo `rodar()` que o Databricks chama."""
    ap = argparse.ArgumentParser(prog="main.py rodar",
                                 description="Executa UMA rodada fim a fim contra o Postgres.")
    ap.add_argument("--run-id", required=True, help="run_id ja inserido em controle.run_request")
    ap.add_argument("--pg", required=True, help="postgresql://user:senha@host:5432/banco")
    ap.add_argument("--blob", default=None, help="destino ADLS/pasta dos snapshots (opcional)")
    ap.add_argument("--service-bus", default=None, help="connection string (opcional)")
    ap.add_argument("--webhook", default=None)
    ap.add_argument("--webhook-token", default=None)
    ap.add_argument("--schema-input", default="input")
    ap.add_argument("--schema-ctrl", default="controle")
    ap.add_argument("--schema-pub", default="public")
    ap.add_argument("--max-time", type=int, default=300, help="segundos do solver (default 300)")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)

    from otimizador.aplicacao.job_databricks import rodar
    r = rodar(a.run_id, a.pg, blob=a.blob,
              schema_input=a.schema_input, schema_ctrl=a.schema_ctrl, schema_pub=a.schema_pub,
              service_bus=a.service_bus, webhook=a.webhook, webhook_token=a.webhook_token,
              max_time_s=a.max_time, workers=a.workers)
    print(r)
    return 0 if r.get("status") == "SUCESSO" else 1


def _cmd_experimento(argv):
    """Rodadas locais de desenvolvimento/analise — sem Postgres, sem Databricks."""
    from otimizador.aplicacao.experimentos_local import main as experimento
    return experimento(argv)


def _cmd_smoke(argv):
    """Pipeline inteiro contra um Postgres real, incluindo a prova de idempotencia."""
    from scripts.smoke_test_postgres import main as smoke
    return smoke(argv)


def _cmd_gerar_ddl(argv):
    """Regenera otimizador/infraestrutura/sql/ddl_resultado.sql a partir das fixtures."""
    from scripts.gerar_ddl_resultado import main as gerar
    gerar()
    return 0


COMANDOS = {
    "rodar":       _cmd_rodar,
    "experimento": _cmd_experimento,
    "smoke":       _cmd_smoke,
    "gerar-ddl":   _cmd_gerar_ddl,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, resto = argv[0], argv[1:]
    if cmd not in COMANDOS:
        print(f"comando desconhecido: {cmd!r}  (use: {' | '.join(COMANDOS)})")
        return 2
    return COMANDOS[cmd](resto)


if __name__ == "__main__":
    sys.exit(main())
