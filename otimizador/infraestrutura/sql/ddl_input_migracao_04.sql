-- Migração 04 — a unidade da capacidade da ETE passa a ser dado, não constante.
--
-- `input.ete_capex.capacidade_por_modulo` sempre foi um número puro, e a unidade dele
-- vivia como conhecimento tácito (litros por segundo). O painel de resultado passou a
-- mostrar quanto de capacidade foi construído, e mostrar isso exige dizer em quê —
-- então a unidade virou coluna: se um dia a medida mudar, muda o cadastro, não o código.
--
-- A soma NÃO muda com a unidade. O que muda é como o número se lê.
--
-- Ela viaja para o snapshot da rodada (`public.otim_sistema.unidade_capacidade`): o
-- cadastro muda, a rodada é imutável, e uma rodada antiga tem de continuar dizendo a
-- unidade que ela usou. Ler do cadastro na hora de exibir mentiria sobre o passado.
--
-- Vazia é resposta válida: a tela mostra a quantidade sem sufixo, em vez de inventar.
--
-- Rode uma vez. Idempotente.

BEGIN;

ALTER TABLE input.ete_capex
  ADD COLUMN IF NOT EXISTS unidade_capacidade text;

COMMENT ON COLUMN input.ete_capex.unidade_capacidade IS
  'Unidade de capacidade_por_modulo e das demais capacidades da ETE. Dado, nao constante de codigo: trocar a medida e mudanca de cadastro. Vazia = a tela mostra o numero sem sufixo.';

COMMIT;
