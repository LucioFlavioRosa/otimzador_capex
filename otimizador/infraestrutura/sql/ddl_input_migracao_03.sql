-- Migração 03 — a sub-bacia passa a dizer o que atende SEM a CTS.
--
-- O PROBLEMA QUE ISTO RESOLVE
--
-- As colunas de ligação e economia da sub-bacia são o que pertence EXCLUSIVAMENTE a
-- ela. A CTS cobre uma área que se SOBREPÕE a essa. Com `usar_cts=false` o motor
-- somava a linha da CTS na sub-bacia — e a ligação da área sobreposta está nas duas
-- linhas, então a soma a contava DUAS VEZES. O universo da meta crescia sozinho ao
-- desligar a CTS, e a cobertura piorava sem nenhuma obra ter mudado.
--
-- Agora a sobreposição é contada UMA VEZ, na entidade que a atende em cada cenário:
--
--   usar_cts=true    a CTS atende a sobreposição; ela está nos números da CTS, que
--                    entra como nó próprio. A sub-bacia usa as colunas exclusivas.
--   usar_cts=false   o coletor não existe; quem atende a sobreposição é a sub-bacia,
--                    e o total dela vem das colunas `*_com_cts`.
--
-- Consequência que vale saber: ligado e desligado deixam de ter a MESMA demanda. Sem o
-- coletor, a parte da área que só ele alcançava não é atendida por ninguém — o que é o
-- comportamento correto, e não uma perda.
--
-- VAZÃO, RECEITA E POPULAÇÃO NÃO são somadas da linha da CTS. Elas são dado da
-- sub-bacia: se desligar o coletor muda a vazão dela, quem atualiza a base é quem
-- cadastra. A escolha de considerar ou não a CTS não mexe em receita — sem o coletor,
-- as ligações que ele atenderia são cobradas pelo ticket da sub-bacia que as absorve.
--
-- MIGRAÇÃO DO DADO. As colunas nascem preenchidas com `exclusiva + CTS pareada`, que é
-- exatamente o que o motor somava em ligações e economias. Nessas duas, portanto, a
-- migração é inerte: o número lido é o mesmo que era calculado. Ela move a conta de
-- dentro do motor para dentro do banco, para que o dia em que a origem trouxer o valor
-- apurado — MENOR onde houver sobreposição real — a mudança seja só de dado.
--
-- O QUE MUDA DE RESULTADO não é esta migração, é a regra nova do motor: vazão, receita e
-- população deixaram de ser somadas. Uma rodada sem CTS passa a usar a vazão e a receita
-- que estiverem na base da sub-bacia.
--
-- Sub-bacia sem CTS pareada recebe a própria quantidade: sem coletor não há
-- sobreposição, e `com_cts` é igual a `exclusiva`.
--
-- NULO NÃO VIRA ZERO. `COALESCE(s.universo_ligacoes, 0) + …` transformaria um universo
-- DESCONHECIDO em "só o que vem da CTS", e o motor usaria esse número como denominador
-- da meta — porque a coluna deixaria de ser nula. Nulo de um lado propaga nulo, e a
-- sub-bacia cai no caminho que ALERTA em vez de calcular sobre dado que não existe.
--
-- Rode uma vez. É idempotente: `IF NOT EXISTS` nas adições e o UPDATE só toca linha
-- cuja coluna nova ainda está nula.

BEGIN;

ALTER TABLE input.subbacia_operacional
  ADD COLUMN IF NOT EXISTS universo_ligacoes_com_cts               integer,
  ADD COLUMN IF NOT EXISTS ligacoes_atuais_com_cts                 integer,
  ADD COLUMN IF NOT EXISTS universo_economias_com_cts              integer,
  ADD COLUMN IF NOT EXISTS economias_atuais_com_cts                integer,
  ADD COLUMN IF NOT EXISTS universo_ligacoes_residencial_com_cts   integer,
  ADD COLUMN IF NOT EXISTS ligacoes_atuais_residencial_com_cts     integer,
  ADD COLUMN IF NOT EXISTS universo_economias_residencial_com_cts  integer,
  ADD COLUMN IF NOT EXISTS economias_atuais_residencial_com_cts    integer;

UPDATE input.subbacia_operacional s SET
  universo_ligacoes_com_cts = CASE WHEN s.universo_ligacoes IS NULL THEN NULL
    ELSE s.universo_ligacoes + COALESCE(k.universo_ligacoes, 0) END,
  ligacoes_atuais_com_cts = CASE WHEN s.ligacoes_atuais IS NULL THEN NULL
    ELSE s.ligacoes_atuais + COALESCE(k.ligacoes_atuais, 0) END,
  universo_economias_com_cts = CASE WHEN s.universo_economias IS NULL THEN NULL
    ELSE s.universo_economias + COALESCE(k.universo_economias, 0) END,
  economias_atuais_com_cts = CASE WHEN s.economias_atuais IS NULL THEN NULL
    ELSE s.economias_atuais + COALESCE(k.economias_atuais, 0) END,
  universo_ligacoes_residencial_com_cts = CASE WHEN s.universo_ligacoes_residencial IS NULL THEN NULL
    ELSE s.universo_ligacoes_residencial + COALESCE(k.universo_ligacoes_residencial, 0) END,
  ligacoes_atuais_residencial_com_cts = CASE WHEN s.ligacoes_atuais_residencial IS NULL THEN NULL
    ELSE s.ligacoes_atuais_residencial + COALESCE(k.ligacoes_atuais_residencial, 0) END,
  universo_economias_residencial_com_cts = CASE WHEN s.universo_economias_residencial IS NULL THEN NULL
    ELSE s.universo_economias_residencial + COALESCE(k.universo_economias_residencial, 0) END,
  economias_atuais_residencial_com_cts = CASE WHEN s.economias_atuais_residencial IS NULL THEN NULL
    ELSE s.economias_atuais_residencial + COALESCE(k.economias_atuais_residencial, 0) END
FROM input.subbacia_cts p
LEFT JOIN input.cts_operacional k ON k.cts = p.cts
WHERE p.sub_bacia = s.sub_bacia
  AND s.universo_ligacoes_com_cts IS NULL;

-- Sem CTS pareada não há sobreposição: `com_cts` é a própria quantidade.
UPDATE input.subbacia_operacional SET
  universo_ligacoes_com_cts  = universo_ligacoes,
  ligacoes_atuais_com_cts    = ligacoes_atuais,
  universo_economias_com_cts = universo_economias,
  economias_atuais_com_cts   = economias_atuais,
  universo_ligacoes_residencial_com_cts  = universo_ligacoes_residencial,
  ligacoes_atuais_residencial_com_cts    = ligacoes_atuais_residencial,
  universo_economias_residencial_com_cts = universo_economias_residencial,
  economias_atuais_residencial_com_cts   = economias_atuais_residencial
WHERE universo_ligacoes_com_cts IS NULL;

COMMENT ON COLUMN input.subbacia_operacional.universo_ligacoes_com_cts IS
  'O que a sub-bacia atende SEM a CTS: exclusivo dela + area sobreposta. NAO e a soma das duas linhas — somar conta a sobreposicao duas vezes. Lida so com usar_cts=false.';

COMMIT;
