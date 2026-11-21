CREATE TABLE model_versions (
  revision text PRIMARY KEY,
  config jsonb NOT NULL,
  note text NOT NULL,
  status text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','active','retired')),
  created_by text REFERENCES accounts(username),
  created_at timestamptz NOT NULL DEFAULT now()
);
-- 同一时刻只允许一个在用版本，并发切换由该唯一索引兜底
CREATE UNIQUE INDEX single_active_model ON model_versions (status) WHERE status='active';

CREATE TABLE model_evaluations (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  candidate_revision text NOT NULL REFERENCES model_versions(revision),
  baseline_revision text NOT NULL,
  batches jsonb NOT NULL,
  kilns jsonb NOT NULL,
  passed boolean NOT NULL,
  failures jsonb NOT NULL,
  created_by text NOT NULL REFERENCES accounts(username),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evaluation_candidate ON model_evaluations (candidate_revision, id DESC);

CREATE TABLE model_releases (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('publish','rollback')),
  from_revision text NOT NULL,
  to_revision text NOT NULL,
  evaluation_id bigint REFERENCES model_evaluations(id),
  rationale text NOT NULL,
  detail jsonb NOT NULL,
  decided_by text NOT NULL REFERENCES accounts(username),
  decided_at timestamptz NOT NULL DEFAULT now(),
  rolled_back_at timestamptz
);

-- 内置初始版本，created_by 为空表示系统提供
INSERT INTO model_versions(revision,config,note,status,created_by)
VALUES ('en-20261121',
        '{"seed":1073,"l1_ratios":[0.15,0.5,0.85,1.0],"alpha_count":40,"folds":5,"test_size":0.25}'::jsonb,
        '初始弹性网络基线','active',NULL)
ON CONFLICT DO NOTHING;
