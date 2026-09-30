-- Migração 02 do schema de RESULTADO — as três parcelas do CAPEX da ETE.
--
-- `public.otim_obra` ganha `capex_terreno`, `capex_modulos_iniciais` e
-- `capex_modulos_expansao`.
--
-- POR QUE ELAS EXISTEM. A tela mostra `quantidade × preço unitário` e confere com o
-- CAPEX da obra — é assim que quem lê o plano sabe de onde o número veio. Na ETE nova
-- essa conta nunca fechou sozinha (falta o terreno), e o serviço a completava
-- derivando o terreno como RESIDUAL: `capex − quantidade × preço`. Residual, e não
-- coluna, de propósito: assim ele não dependia do TEXTO que o motor escreveu na chave
-- do `capex_componentes`.
--
-- Em 29/09/2026 o cliente pediu que os módulos iniciais da ETE nova e os de expansão
-- tenham vazão e preço PRÓPRIOS. Com dois preços na mesma ETE, `quantidade × preço`
-- deixa de ter um preço só para usar, e o residual passa a misturar terreno com a
-- diferença entre os dois preços: num pacote de 300.000 de terreno + 1 módulo de
-- 500.000 + 1 de expansão de 260.000, o residual dava 60.000 de "terreno".
--
-- Decisão do dono do produto: publicar as parcelas separadas, para a conta continuar
-- fechando na tela — terreno + módulos iniciais + módulos de expansão = CAPEX.
--
-- NULAS em toda obra que não é ETE, e isso é informação: nelas `quantidade × preço`
-- fecha exato, e uma coluna de zeros na tela pediria explicação que não existe.
--
-- RODADAS JÁ PUBLICADAS ficam com as três nulas, e é o correto. O serviço mantém o
-- cálculo residual como fallback para elas — nenhuma tem módulos de dois preços, então
-- ali o residual É o terreno.
--
-- Rode uma vez. Idempotente.

BEGIN;

ALTER TABLE public.otim_obra
  ADD COLUMN IF NOT EXISTS capex_terreno          double precision,
  ADD COLUMN IF NOT EXISTS capex_modulos_iniciais double precision,
  ADD COLUMN IF NOT EXISTS capex_modulos_expansao double precision;

COMMENT ON COLUMN public.otim_obra.capex_terreno IS
  'Parcela do CAPEX que e terreno (so a ETE nova tem). Nula fora da ETE, e em rodada publicada antes da coluna existir — ali o servico a deriva como residual.';
COMMENT ON COLUMN public.otim_obra.capex_modulos_iniciais IS
  'Parcela do CAPEX dos modulos ao preco de capex_por_modulo. Numa ETE existente todo modulo e desta parcela: a separacao e pelo PRECO pago, e nao pela fase.';
COMMENT ON COLUMN public.otim_obra.capex_modulos_expansao IS
  'Parcela do CAPEX dos modulos ao preco de capex_por_modulo_expansao (so a ETE nova). Zero quando a coluna do cadastro esta vazia, porque ai os dois precos sao o mesmo.';

COMMIT;
