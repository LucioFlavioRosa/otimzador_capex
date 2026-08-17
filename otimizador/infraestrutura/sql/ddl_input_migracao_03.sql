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
-- POPULAÇÃO, VAZÃO E RECEITA continuam sendo somadas da linha da CTS: não há coluna
-- consolidada para elas na origem. Onde houver sobreposição real, elas ficam com a
-- dupla contagem que as de cima deixaram de ter. É uma incoerência CONHECIDA entre
-- grandezas, registrada aqui e no motor — não um esquecimento.
--
-- MIGRAÇÃO DO DADO. As colunas nascem preenchidas com `exclusiva + CTS pareada`, que é
-- exatamente o que o motor já somava. Ou seja: **esta migração não muda resultado
-- nenhum**. Ela só move a conta de dentro do motor para dentro do banco, para que o dia
-- em que a origem trouxer o valor apurado — que vai ser MENOR onde houver sobreposição
-- real — a mudança seja só de dado, não de código.
--
-- Sub-bacia sem CTS pareada recebe a própria quantidade: sem coletor não há
-- sobreposição, e `com_cts` é igual a `exclusiva`.
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
  universo_ligacoes_com_cts  = COALESCE(s.universo_ligacoes, 0)  + COALESCE(k.universo_ligacoes, 0),
  ligacoes_atuais_com_cts    = COALESCE(s.ligacoes_atuais, 0)    + COALESCE(k.ligacoes_atuais, 0),
  universo_economias_com_cts = COALESCE(s.universo_economias, 0) + COALESCE(k.universo_economias, 0),
  economias_atuais_com_cts   = COALESCE(s.economias_atuais, 0)   + COALESCE(k.economias_atuais, 0),
  universo_ligacoes_residencial_com_cts  = COALESCE(s.universo_ligacoes_residencial, 0)
                                         + COALESCE(k.universo_ligacoes_residencial, 0),
  ligacoes_atuais_residencial_com_cts    = COALESCE(s.ligacoes_atuais_residencial, 0)
                                         + COALESCE(k.ligacoes_atuais_residencial, 0),
  universo_economias_residencial_com_cts = COALESCE(s.universo_economias_residencial, 0)
                                         + COALESCE(k.universo_economias_residencial, 0),
  economias_atuais_residencial_com_cts   = COALESCE(s.economias_atuais_residencial, 0)
                                         + COALESCE(k.economias_atuais_residencial, 0)
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
