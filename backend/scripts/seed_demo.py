from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from kiln.infra import audit, connect, password_digest
from kiln.ledger import active_model, load_snapshot, snapshot_digest
from kiln.regression import estimate

SHOP_TZ = timezone(timedelta(hours=8))


def sign_off(conn, code):
    """把数据完整的批次登记为已试算并签核，供新旧模型对照评估取样。"""
    batch = conn.execute('SELECT * FROM firing_batches WHERE code=%s', (code,)).fetchone()
    curves = conn.execute('SELECT * FROM kiln_curve WHERE batch_code=%s ORDER BY started_at', (code,)).fetchall()
    samples = conn.execute('SELECT * FROM gas_meter WHERE batch_code=%s ORDER BY sampled_at', (code,)).fetchall()
    snapshot = load_snapshot(batch, curves, samples, active_model(conn))
    digest = snapshot_digest(snapshot)
    result = estimate(snapshot)
    run = conn.execute('''INSERT INTO load_runs(batch_code,status,requested_by,input_hash,input_snapshot,result,attempts)
        VALUES (%s,'ready','firing',%s,%s,%s,1) RETURNING id''',
        (code, digest, Jsonb(snapshot), Jsonb(result))).fetchone()
    conn.execute('''INSERT INTO cycle_reviews(batch_code,run_id,next_minutes,attribution,rationale,proposed_by,confirmed_by,confirmed_at)
        VALUES (%s,%s,475,'observed',%s,'firing','chief',now())''',
        (code, run['id'], '实测与模型参考的偏差在交班容许范围内，下一周期按 475 分钟交接'))
    conn.execute("UPDATE firing_batches SET status='confirmed' WHERE code=%s", (code,))
    audit(conn, code, 'firing', 'load_queued', run_id=run['id'], input_hash=digest)
    audit(conn, code, 'worker', 'load_ready', run_id=run['id'], delta_m3=result['delta_m3'])
    audit(conn, code, 'chief', 'cycle_confirmed', run_id=run['id'], input_hash=digest, change_id=None, minutes=475)


def seed():
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('kiln-demo-seed'))")
        for username, role in [('firing', 'operator'), ('chief', 'supervisor'), ('energy', 'viewer')]:
            present = conn.execute('SELECT 1 FROM accounts WHERE username=%s', (username,)).fetchone()
            if not present:
                conn.execute('INSERT INTO accounts VALUES (%s,%s,%s)', (username, password_digest('KilnLab!1073'), role))
        for day, code, kiln, mass, base, total, completeness in [
            (21, 'K2-1121-A', 'K2 辊道窑', 1170, 1250.400, 112.700, 'gap'),
            (20, 'K2-1120-B', 'K2 辊道窑', 1260, 1095.100, 155.300, 'full'),
            (21, 'K1-1121-C', 'K1 辊道窑', 910, 450.700, 121.400, 'scheduled'),
        ]:
            start = datetime(2026, 11, day, 6, tzinfo=SHOP_TZ)
            end = start + timedelta(hours=8)
            added = conn.execute('''INSERT INTO firing_batches VALUES (%s,%s,'釉面砖 Y17',3,%s,%s,%s,480,%s,'firing')
                ON CONFLICT DO NOTHING RETURNING code''', (code, kiln, mass, start, end,
                                                            'scheduled' if completeness == 'scheduled' else 'recording')).fetchone()
            if not added or completeness == 'scheduled':
                continue
            for phase, a, b, peak in [('ramp', 0, 3, 1050), ('soak', 3, 5, 1180), ('cool', 5, 8, 1000)]:
                if completeness == 'gap' and phase == 'soak':
                    continue
                conn.execute('INSERT INTO kiln_curve VALUES (%s,%s,%s,%s,%s,%s,\'firing\')',
                             (code, phase, start + timedelta(hours=a), start + timedelta(hours=b), peak, f'{code}-TC.csv'))
            for hours, fraction in [(0, 0), (2, .27), (4, .61), (6, .87), (8, 1)]:
                if completeness == 'gap' and hours == 4:
                    continue
                conn.execute('INSERT INTO gas_meter VALUES (%s,%s,\'GM-K2-08\',%s,%s,\'firing\')',
                             (code, start + timedelta(hours=hours), round(base + total * fraction, 3), f'{code}-GAS.csv'))
            if completeness == 'full':
                sign_off(conn, code)
        counts = {name: conn.execute(f'SELECT count(*) AS n FROM {name}').fetchone()['n'] for name in
                  ('accounts', 'firing_batches', 'kiln_curve', 'gas_meter', 'recipe_changes', 'load_runs',
                   'cycle_reviews', 'ledger_audit', 'model_versions', 'model_evaluations', 'model_releases')}
        print('seed ledger', counts)


if __name__ == '__main__':
    seed()
