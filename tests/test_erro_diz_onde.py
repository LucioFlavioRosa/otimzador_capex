"""O ERRO GRAVADO NO BANCO DIZ ONDE, E NÃO SÓ O QUÊ.

Pergunta do dono do produto em 30/09/2026, antes de subir na Azure: *"se depois do deploy
der algum erro de código, eu conseguiria via log localizar possíveis localizações dos erros
no código?"*

Medido: `controle.run_status.erro` guardava `f"{type(e).__name__}: {e}"` — o QUE falhou,
nunca o ONDE. O traceback completo existe, mas só no `print` que vai para o log do driver do
Databricks: ele expira, e quem opera por VPN chega ao Postgres com um `psql` antes de chegar
a um log de job.

Agora a causa carrega os três últimos quadros da pilha, e a localização vem ANTES da
mensagem — o serviço corta a causa em 500 caracteres ao servir (`causa_segura`), e uma
mensagem longa de SQL empurraria o `arquivo.py:linha` para fora do corte.

Sem caminho absoluto: `arquivo.py` localiza, o resto é mapa da máquina. O que não é gravado
não vaza.
"""
import re

from otimizador.aplicacao import job_databricks as J


def _estourar(profundidade=3):
    """Uma pilha conhecida, para conferir os quadros que saem."""
    def fundo():
        raise ValueError("deu ruim no fundo")

    def meio():
        fundo()

    def topo():
        meio()

    try:
        topo()
    except ValueError as e:
        return e


def test_a_causa_diz_ARQUIVO_E_LINHA():
    e = _estourar()
    onde = J._onde_estourou(e.__traceback__)
    assert "test_erro_diz_onde.py:" in onde, onde
    assert re.search(r"test_erro_diz_onde\.py:\d+ em fundo", onde), onde


def test_os_quadros_vem_DO_MAIS_PROFUNDO_para_o_mais_raso():
    """Onde a exceção nasceu primeiro. O topo da pilha é sempre `rodar()`, que não diz
    nada; o fundo é o que responde a pergunta."""
    onde = J._onde_estourou(_estourar().__traceback__)
    partes = onde.split(" <- ")
    assert len(partes) == 3, partes
    assert " em fundo" in partes[0]
    assert " em meio" in partes[1]
    assert " em topo" in partes[2]


def test_NAO_vai_caminho_absoluto():
    """`arquivo.py` localiza; o caminho inteiro é mapa da máquina. E o serviço também
    corta caminho ao servir, mas esta função não depende disso."""
    onde = J._onde_estourou(_estourar().__traceback__)
    assert "\\" not in onde and "/" not in onde, onde


def test_a_LOCALIZACAO_sobrevive_ao_corte_de_500_do_servico():
    """A mensagem entra depois da localização justamente por isto: um erro de SQL do
    Postgres passa fácil de 500 caracteres, e o corte comeria a parte útil."""
    e = _estourar()
    mensagem_longa = "x" * 2000
    causa = f"{type(e).__name__} em {J._onde_estourou(e.__traceback__)}: {mensagem_longa}"
    assert "test_erro_diz_onde.py:" in causa[:500]
    assert causa.startswith("ValueError em ")


def test_o_job_grava_a_causa_COM_a_localizacao(monkeypatch):
    """O caminho de verdade: `rodar()` falhando marca ERRO com o arquivo e a linha."""
    gravado = {}

    def marcar(pg, rid, status, erro=None, schema="controle"):
        gravado[status] = erro

    class _Pub:
        marcar_status_controle = staticmethod(marcar)

    import sys as _sys
    import otimizador.infraestrutura as _infra
    monkeypatch.setattr(_infra, "publicacao", _Pub, raising=False)
    monkeypatch.setitem(_sys.modules, "otimizador.infraestrutura.publicacao", _Pub)

    def explodir(pg, rid, schema="controle"):
        raise RuntimeError("run_request nao encontrada")

    monkeypatch.setattr(J, "_ler_run_request", explodir)
    try:
        J.rodar("run_x", "postgresql://dublê")
    except RuntimeError:
        pass
    assert "ERRO" in gravado, gravado
    causa = gravado["ERRO"]
    assert causa.startswith("RuntimeError em "), causa
    assert "job_databricks.py:" in causa, causa
    assert "run_request nao encontrada" in causa, causa


def test_QUADRO_DE_BIBLIOTECA_nao_rouba_a_frente():
    """O cenário de produção, e o defeito que a revisão 7 achou.

    Num `INSERT` que falha, a pilha termina dentro do driver, e a causa começava com
    `extras.py:1299 em execute_values` — nome que não ajuda quem vai abrir um arquivo do
    motor.

    O erro aqui nasce DENTRO do motor (`_exigir_colunas_do_resultado`, que abre conexão) e
    termina fundo na biblioteca de banco: é a forma exata da falha real. O que tem de
    aparecer primeiro é o arquivo do MOTOR.
    """
    try:
        J._exigir_colunas_do_resultado("postgresql://ninguem:nada@127.0.0.1:1/naoexiste")
    except Exception as e:
        onde = J._onde_estourou(e.__traceback__)
    else:
        pytest.fail("a conexão deveria falhar")
    assert onde.startswith("job_databricks.py:"), onde


def test_a_raiz_do_motor_vale_TAMBEM_instalado_como_wheel():
    """A regra é POSITIVA — "está sob a raiz do pacote?" — e não negativa.

    A primeira versão perguntava "não está nos caminhos do `sysconfig`?", e `sysconfig`
    inclui `purelib`/`platlib`, que são `site-packages`. No Databricks o motor é instalado
    como wheel, dentro de `site-packages`: o filtro descartava os quadros do próprio motor
    justamente no ambiente de produção.

    O teste não instala wheel; ele prende a propriedade que faz a regra sobreviver a isso —
    a raiz sai do próprio módulo, então acompanha o pacote para onde ele for.
    """
    import os
    raizes = J._raizes_do_motor()
    assert raizes, "sem raiz, todo quadro seria descartado"
    meu = os.path.dirname(os.path.abspath(J.__file__)).replace("\\", "/").lower()
    assert any(meu.startswith(r) for r in raizes), (meu, raizes)
    # e a raiz NÃO é um caminho de biblioteca genérico, que pegaria tudo
    assert not any(r.endswith("/site-packages") for r in raizes), raizes


def test_se_NADA_for_do_motor_ainda_diz_algo(monkeypatch):
    """O fallback: falha inteiramente dentro de dependência ou do chamador.

    Nome de biblioteca ainda é melhor do que string vazia — e sem isto a causa gravada no
    banco perderia a única pista que tinha.

    A revisão 7 apontou que o meu teste anterior disto era VAZIO: ele montava a pilha e
    nunca chamava `_onde_estourou`. Agora a raiz do motor é trocada por uma pasta que não
    existe, e aí nenhum quadro é nosso — que é o estado que o fallback atende.
    """
    monkeypatch.setattr(J, "_raizes_do_motor", lambda: ("/pasta/que/nao/existe",))
    e = _estourar()
    onde = J._onde_estourou(e.__traceback__)
    assert onde, "o fallback não pode devolver vazio"
    assert "test_erro_diz_onde.py:" in onde, onde
