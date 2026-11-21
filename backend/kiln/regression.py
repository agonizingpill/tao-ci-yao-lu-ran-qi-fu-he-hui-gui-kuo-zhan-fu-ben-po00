import hashlib
from datetime import datetime

import numpy as np
from sklearn.linear_model import ElasticNetCV
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import KFold, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SEED = 1073
FEATURES = ['batch_kg', 'ramp_hours', 'soak_hours', 'cool_hours', 'ramp_peak_c', 'soak_peak_c', 'cool_peak_c']
DEFAULT_REVISION = 'en-20261121'
DEFAULT_CONFIG = {'seed': SEED, 'l1_ratios': [.15, .5, .85, 1.0], 'alpha_count': 40, 'folds': 5, 'test_size': .25}


def duration_hours(start, end):
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 3600


def historical_batches():
    rng = np.random.default_rng(SEED)
    mass = rng.uniform(700, 1600, 160)
    ramp = rng.uniform(1.5, 4.5, 160)
    soak = rng.uniform(.6, 2.8, 160)
    cool = rng.uniform(1, 4, 160)
    ramp_peak = rng.uniform(840, 1120, 160)
    soak_peak = ramp_peak + rng.uniform(40, 165, 160)
    cool_peak = soak_peak - rng.uniform(60, 220, 160)
    x = np.column_stack((mass, ramp, soak, cool, ramp_peak, soak_peak, cool_peak))
    target_m3 = (24 + .014 * mass + 11 * ramp + 16 * soak + 3.5 * cool +
                 .035 * ramp_peak + .025 * soak_peak + rng.normal(0, 2.4, len(mass)))
    return x, target_m3


def fit(config):
    """按版本配置拟合弹性网络，返回管线、留出集与训练数据。"""
    x, y = historical_batches()
    train_x, test_x, train_y, test_y = train_test_split(x, y, test_size=config['test_size'],
                                                        random_state=config['seed'])
    cv = KFold(config['folds'], shuffle=True, random_state=config['seed'])
    model = make_pipeline(StandardScaler(), ElasticNetCV(l1_ratio=list(config['l1_ratios']),
                          alphas=np.logspace(-3, 1, int(config['alpha_count'])), cv=cv, max_iter=10000,
                          random_state=config['seed']))
    model.fit(train_x, train_y)
    return {'model': model, 'cv': cv, 'x': x, 'y': y,
            'train_y': train_y, 'test_x': test_x, 'test_y': test_y}


def snapshot_features(snapshot):
    curves = snapshot['curve']
    features = [float(snapshot['cycle']['batch_kg'])]
    features += [duration_hours(c['started_at'], c['ended_at']) for c in curves]
    features += [float(c['peak_c']) for c in curves]
    return features


def snapshot_actual(snapshot):
    meter = snapshot['meter']
    return round(float(meter[-1]['reading_m3']) - float(meter[0]['reading_m3']), 3)


def predict(model, snapshot):
    return round(float(model.predict([snapshot_features(snapshot)])[0]), 3)


def estimate(snapshot):
    config = snapshot.get('model_config') or DEFAULT_CONFIG
    fitted = fit(config)
    model, cv = fitted['model'], fitted['cv']
    step = model.named_steps['elasticnetcv']
    prediction = predict(model, snapshot)
    actual = snapshot_actual(snapshot)
    return {'expected_m3': prediction, 'actual_m3': actual, 'delta_m3': round(actual - prediction, 3),
            'mae_m3': round(float(mean_absolute_error(fitted['test_y'], model.predict(fitted['test_x']))), 3),
            'seed': config['seed'], 'train_count': len(fitted['train_y']), 'holdout_count': len(fitted['test_y']),
            'cv_folds': cv.n_splits, 'alpha': float(step.alpha_), 'l1_ratio': float(step.l1_ratio_),
            'training_sha256': hashlib.sha256(np.column_stack((fitted['x'], fitted['y'])).astype('<f8').tobytes()).hexdigest(),
            'features': dict(zip(FEATURES, snapshot_features(snapshot))),
            'coefs_scaled': dict(zip(FEATURES, map(float, step.coef_))),
            'model_revision': snapshot.get('model_revision', DEFAULT_REVISION)}
