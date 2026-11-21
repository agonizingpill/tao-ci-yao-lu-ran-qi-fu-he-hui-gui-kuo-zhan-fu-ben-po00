"""新旧模型对照门禁：缺预测与分窑号平均绝对误差比较。"""


def compare_predictions(records):
    """对照同一批已签核窑次的新旧预测。

    records 每项为 {code, kiln, actual_m3, old_m3, new_m3}，或带 error 的缺预测记录。
    返回 (kilns, batches, failures)：任一窑次缺预测记 MISSING_PREDICTION，
    任一窑号新模型 MAE 高于在用模型记 MAE_REGRESSED，failures 非空即拦截发布。
    """
    kilns, batches, failures = [], [], []
    by_kiln = {}
    for record in records:
        batch = {'code': record['code'], 'kiln': record['kiln'], 'run_id': record.get('run_id'),
                 'actual_m3': record.get('actual_m3'), 'old_m3': record.get('old_m3'),
                 'new_m3': record.get('new_m3')}
        missing = (record.get('error') or record.get('actual_m3') is None or
                   record.get('old_m3') is None or record.get('new_m3') is None)
        if missing:
            batch['error'] = record.get('error') or '缺预测'
            failures.append({'code': 'MISSING_PREDICTION', 'batch': record['code'],
                             'message': f"窑次 {record['code']} 缺少新旧模型对照预测"})
        else:
            kiln = by_kiln.setdefault(record['kiln'],
                                      {'kiln': record['kiln'], 'batches': 0, 'old_abs': .0, 'new_abs': .0})
            kiln['batches'] += 1
            kiln['old_abs'] += abs(record['old_m3'] - record['actual_m3'])
            kiln['new_abs'] += abs(record['new_m3'] - record['actual_m3'])
        batches.append(batch)
    for kiln in by_kiln.values():
        old_mae = round(kiln['old_abs'] / kiln['batches'], 3)
        new_mae = round(kiln['new_abs'] / kiln['batches'], 3)
        kilns.append({'kiln': kiln['kiln'], 'batches': kiln['batches'], 'old_mae': old_mae, 'new_mae': new_mae})
        if new_mae > old_mae:
            failures.append({'code': 'MAE_REGRESSED', 'kiln': kiln['kiln'],
                             'message': f"窑号{kiln['kiln']}平均绝对误差由 {old_mae} 升至 {new_mae}"})
    if not records:
        failures.append({'code': 'MISSING_PREDICTION', 'message': '没有已签核窑次可供新旧模型对照'})
    return kilns, batches, failures
