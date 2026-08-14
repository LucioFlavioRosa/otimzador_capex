-- Migração 01 do schema de RESULTADO — a unidade de capacidade no snapshot.
--
-- `public.otim_sistema` ganha `unidade_capacidade`, congelada por rodada. Ela vem do
-- cadastro (`input.ete_capex.unidade_capacidade`, migração 04) e é publicada junto com
-- os números da rodada, porque o cadastro muda e a rodada é imutável: uma rodada antiga
-- tem de continuar dizendo a unidade que ELA usou.
--
-- Rodadas JÁ PUBLICADAS ficam com a coluna nula, e é o correto — ninguém sabe em que
-- unidade elas foram feitas. O painel mostra a quantidade sem sufixo nesse caso, em vez
-- de atribuir a elas uma unidade escolhida depois.
--
-- Rode uma vez. Idempotente.

BEGIN;

ALTER TABLE public.otim_sistema
  ADD COLUMN IF NOT EXISTS unidade_capacidade text;

COMMENT ON COLUMN public.otim_sistema.unidade_capacidade IS
  'Unidade da capacidade, congelada nesta rodada. Nula em rodada publicada antes da coluna existir: a tela mostra o numero sem sufixo, em vez de atribuir unidade escolhida depois.';

COMMIT;
