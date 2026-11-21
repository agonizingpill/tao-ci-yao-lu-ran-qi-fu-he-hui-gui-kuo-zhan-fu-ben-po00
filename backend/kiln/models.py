"""燃气回归模型版本登记、对照评估、发布与回滚。"""

import math

from psycopg.types.json import Jsonb

from .infra import audit
from .ledger import active_model, require
from .model_gates import compare_predictions
from .regression import DEFAULT_CONFIG, fit, predict, snapshot_actual

RELEASE_LOCK = 'kiln-model-release'


def registry(conn):
    versions = conn.execute('''SELECT revision,config,note,status,created_by,created_at
        FROM model_versions ORDER BY created_at,revision''').fetchall()
    evaluations = conn.execute('''SELECT DISTINCT ON (candidate_revision) *
        FROM model_evaluations ORDER BY candidate_revision,id DESC''').fetchall()
    by_candidate = {row['candidate_revision']: row for row in evaluations}
    releases = conn.execute('SELECT * FROM model_releases ORDER BY id DESC LIMIT 50').fetchall()
    active = next((row['revision'] for row in versions if row['status'] == 'active'), None)
    return {'active': active,
            'versions': [{**row, 'evaluation': by_candidate.get(row['revision'])} for row in versions],
            'releases': releases}


def register_model(conn, form, actor):
    config = {'seed': form.seed, 'l1_ratios': form.l1_ratios, 'alpha_count': DEFAULT_CONFIG['alpha_count'],
              'folds': form.folds, 'test_size': DEFAULT_CONFIG['test_size']}
    row = conn.execute('''INSERT INTO model_versions(revision,config,note,created_by)
        VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING revision,status''',
        (form.revision, Jsonb(config), form.note, actor)).fetchone()
    require(row, 'DUPLICATE_MODEL', '模型版本号已存在')
    audit(conn, None, actor, 'model_registered', revision=form.revision, seed=form.seed, folds=form.folds)
    return row


def confirmed_snapshots(conn):
    """已签核窑次及其签核试算快照，作为新旧模型对照的固定样本。"""
    return conn.execute('''SELECT b.code,b.kiln,cr.run_id,r.status AS run_status,r.input_snapshot
        FROM firing_batches b
        JOIN cycle_reviews cr ON cr.batch_code=b.code AND cr.confirmed_at IS NOT NULL
        LEFT JOIN load_runs r ON r.id=cr.run_id
        WHERE b.status='confirmed' ORDER BY b.code''').fetchall()


def evaluate_model(conn, revision, actor):
    candidate = conn.execute('SELECT * FROM model_versions WHERE revision=%s', (revision,)).fetchone()
    require(candidate, 'UNKNOWN_MODEL', '模型版本不存在', 404)
    require(candidate['status'] == 'candidate', 'MODEL_NOT_CANDIDATE', '只有候选版本需要对照评估')
    baseline = active_model(conn)
    old_fit, new_fit = fit(baseline['config']), fit(candidate['config'])
    records = []
    for item in confirmed_snapshots(conn):
        record = {'code': item['code'], 'kiln': item['kiln'], 'run_id': item['run_id']}
        snapshot = item['input_snapshot']
        try:
            if item['run_status'] != 'ready' or not snapshot:
                raise ValueError('签核试算缺失')
            record['actual_m3'] = snapshot_actual(snapshot)
            record['old_m3'] = predict(old_fit['model'], snapshot)
            record['new_m3'] = predict(new_fit['model'], snapshot)
            if not all(math.isfinite(record[key]) for key in ('actual_m3', 'old_m3', 'new_m3')):
                raise ValueError('预测结果无效')
        except Exception as exc:
            record['error'] = type(exc).__name__
        records.append(record)
    kilns, batches, failures = compare_predictions(records)
    row = conn.execute('''INSERT INTO model_evaluations(candidate_revision,baseline_revision,batches,kilns,passed,failures,created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at''',
        (revision, baseline['revision'], Jsonb(batches), Jsonb(kilns), not failures, Jsonb(failures), actor)).fetchone()
    audit(conn, None, actor, 'model_evaluated', revision=revision, baseline=baseline['revision'],
          evaluation_id=row['id'], passed=not failures, failures=len(failures))
    return {'id': row['id'], 'candidate': revision, 'baseline': baseline['revision'],
            'passed': not failures, 'kilns': kilns, 'batches': batches, 'failures': failures}


def publish_model(conn, revision, rationale, actor):
    # 咨询锁串行化切换：并发发布只先生效一个，其余按冲突拦截
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (RELEASE_LOCK,))
    candidate = conn.execute('SELECT * FROM model_versions WHERE revision=%s FOR UPDATE', (revision,)).fetchone()
    require(candidate, 'UNKNOWN_MODEL', '模型版本不存在', 404)
    baseline = conn.execute("SELECT * FROM model_versions WHERE status='active' FOR UPDATE").fetchone()
    require(baseline, 'NO_ACTIVE_MODEL', '没有在用的燃气回归模型版本', 500)
    require(candidate['status'] != 'active', 'ALREADY_RELEASED', '该版本已在用，重复或并发发布不再生效')
    require(candidate['status'] == 'candidate', 'MODEL_NOT_CANDIDATE', '已退役版本不能再次发布，请登记新的候选版本')
    evaluation = conn.execute('''SELECT * FROM model_evaluations WHERE candidate_revision=%s
        ORDER BY id DESC LIMIT 1''', (revision,)).fetchone()
    require(evaluation, 'EVALUATION_REQUIRED', '请先对在用版本完成新旧对照评估')
    require(evaluation['baseline_revision'] == baseline['revision'], 'STALE_EVALUATION',
            '在用版本已变化，旧评估需重算后再发布')
    require(evaluation['passed'], 'EVALUATION_BLOCKED',
            '对照评估未通过：' + '；'.join(item['message'] for item in evaluation['failures']))
    # 同事务切换：任一步失败整体回滚，保留旧版在用
    conn.execute("UPDATE model_versions SET status='retired' WHERE revision=%s", (baseline['revision'],))
    conn.execute("UPDATE model_versions SET status='active' WHERE revision=%s", (revision,))
    release = conn.execute('''INSERT INTO model_releases(kind,from_revision,to_revision,evaluation_id,rationale,detail,decided_by)
        VALUES ('publish',%s,%s,%s,%s,%s,%s) RETURNING *''',
        (baseline['revision'], revision, evaluation['id'], rationale,
         Jsonb({'kilns': evaluation['kilns'], 'batches': len(evaluation['batches'])}), actor)).fetchone()
    audit(conn, None, actor, 'model_published', from_revision=baseline['revision'], to_revision=revision,
          evaluation_id=evaluation['id'])
    return release


def rollback_model(conn, note, actor):
    # 只回切在用版本指针，不改写任何试算结果与已签核复核
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (RELEASE_LOCK,))
    baseline = conn.execute("SELECT * FROM model_versions WHERE status='active' FOR UPDATE").fetchone()
    require(baseline, 'NO_ACTIVE_MODEL', '没有在用的燃气回归模型版本', 500)
    last = conn.execute('''SELECT * FROM model_releases WHERE kind='publish' AND rolled_back_at IS NULL
        ORDER BY id DESC LIMIT 1 FOR UPDATE''').fetchone()
    require(last, 'NOTHING_TO_ROLLBACK', '没有可回滚的模型发布')
    require(last['to_revision'] == baseline['revision'], 'ROLLBACK_STALE', '在用版本与最近发布不一致，无法回滚')
    target = conn.execute('SELECT * FROM model_versions WHERE revision=%s FOR UPDATE',
                          (last['from_revision'],)).fetchone()
    require(target, 'UNKNOWN_MODEL', '回滚目标版本不存在', 500)
    conn.execute("UPDATE model_versions SET status='retired' WHERE revision=%s", (baseline['revision'],))
    conn.execute("UPDATE model_versions SET status='active' WHERE revision=%s", (target['revision'],))
    conn.execute('UPDATE model_releases SET rolled_back_at=now() WHERE id=%s', (last['id'],))
    release = conn.execute('''INSERT INTO model_releases(kind,from_revision,to_revision,evaluation_id,rationale,detail,decided_by)
        VALUES ('rollback',%s,%s,NULL,%s,%s,%s) RETURNING *''',
        (baseline['revision'], target['revision'], note,
         Jsonb({'reverted_release_id': last['id']}), actor)).fetchone()
    audit(conn, None, actor, 'model_rolled_back', from_revision=baseline['revision'],
          to_revision=target['revision'], reverted_release_id=last['id'])
    return release
