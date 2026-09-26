-- 0006: who decided an ingest — the similarity rule, or the learner on the
-- review screen.
--
-- The review screen now shows duplicate candidates before a concept is
-- accepted and lets the learner merge it or create a new node, whatever the
-- 0.6 rule says. Recording which decisions came from a person is what makes the
-- rule measurable: a learner choosing "create" on a candidate at or above the
-- threshold is a false positive of the rule, in the log, with its score.
ALTER TABLE ks.ingest_log
  ADD COLUMN chosen_by TEXT NOT NULL DEFAULT 'rule' CHECK (chosen_by IN ('rule', 'learner'));
