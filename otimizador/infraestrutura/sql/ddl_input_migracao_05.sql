-- Migração 05 — componente pode existir SEM sistema.
--
-- O QUE MUDA. `sistema_topologia.sistema_id` deixa de ser NOT NULL. Uma linha com
-- sistema nulo passa a significar "componente cadastrado, ainda não colocado em
-- nenhum sistema".
--
-- POR QUÊ. Do Databricks vêm quais sub-bacias e qual ETE pertencem ao sistema, e
-- TODAS as CTS cadastradas — mas não em que sistema cada CTS entra. Isso quem
-- monta é a Regional, junto com o caminho até a ETE
-- (`componente_sistema_id_jusante`), que também nunca veio de fora. Sem esta
-- migração, "tirar a CTS do sistema" só poderia ser apagar a linha, e a linha é o
-- único lugar onde o NOME do componente existe: `componente_sistema_nome` não tem
-- equivalente em `cts_operacional`, `subbacia_operacional` nem `ete_capex`.
-- Apagar para desvincular perderia o nome, e a lista de CTS disponíveis viraria
-- uma lista de ids.
--
-- O MOTOR NÃO PRECISA MUDAR, e isso não é sorte: ele monta os nós com
--
--     for d in L("sistema-topologia"):
--         if d.get("sistema_id") not in sis_cid: continue
--
-- e `sis_cid` é indexado por `sistema_id` (vem de `cidade-sistema`). `None` nunca
-- é chave dele, então o componente sem sistema é pulado — não vira nó, não entra
-- na cobertura, não recebe obra. É exatamente o comportamento desejado: quem não
-- foi colocado em sistema nenhum não participa da simulação. Vale igual para a
-- planilha, onde a célula vazia chega como NaN.
--
-- A FK CONTINUA. `sistema_id` nulo não é violação de chave estrangeira — o
-- Postgres não a exige em coluna nula. Quem tem sistema continua obrigado a
-- apontar para um sistema que existe.
--
-- CUIDADO AO LER ESTA COLUNA. Consulta que faz `JOIN cidade_sistema USING
-- (sistema_id)` passa a PERDER as linhas sem sistema, silenciosamente. Isso é o
-- certo para escopo por unidade (componente sem sistema não é de unidade
-- nenhuma), e é errado para "listar tudo que existe" — aí o JOIN precisa ser
-- LEFT. As duas leituras existem no backend e estão marcadas lá.
--
-- Idempotente: `DROP NOT NULL` numa coluna que já aceita nulo não faz nada.

BEGIN;

ALTER TABLE input.sistema_topologia ALTER COLUMN sistema_id DROP NOT NULL;

COMMENT ON COLUMN input.sistema_topologia.sistema_id IS
  'Em que sistema o componente está. NULO = cadastrado e ainda não colocado em sistema nenhum — o motor o ignora. Para sub-bacia e ETE vem do Databricks; para CTS quem preenche é a Regional.';

COMMENT ON COLUMN input.sistema_topologia.componente_sistema_id_jusante IS
  'Para onde este componente escoa, dentro do MESMO sistema. Nulo na ETE, que é o fim do caminho. Não vem do Databricks: é a Regional que monta o caminho até a ETE.';

COMMIT;
