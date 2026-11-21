import {state} from './state.js';
import {amount, badge, clock, date, empty, esc, field, table, ticket} from './ui.js';
import {readingTrace} from './firing.js';

const showLoad = (result) => {
  const span = Math.max(result.expected_m3, result.actual_m3, 1) * 1.12;
  return `<figure class="gas-comparison"><figcaption>本周期燃气量 <span>m³</span></figcaption>
    <div class="gas-row"><span>模型参考</span><i style="width:${100 * Math.max(0, result.expected_m3) / span}%"></i><b>${amount(result.expected_m3)}</b></div>
    <div class="gas-row actual"><span>表计实测</span><i style="width:${100 * result.actual_m3 / span}%"></i><b>${amount(result.actual_m3)}</b></div>
    <div class="delta"><span>实测 − 参考</span><b>${result.delta_m3 > 0 ? '+' : ''}${amount(result.delta_m3)} m³</b></div></figure>`;
};

const modelStatus = {candidate: ['pending', '候选'], active: ['ready', '在用'], retired: ['scheduled', '已退役']};

function evaluationCell(version, active) {
  const evaluation = version.evaluation;
  if (!evaluation) return '<p class="muted">尚未与在用版本对照评估。</p>';
  const stale = evaluation.baseline_revision !== active;
  const kilns = (evaluation.kilns || []).map(k => `${esc(k.kiln)} MAE ${amount(k.old_mae)} → ${amount(k.new_mae)}`).join('；');
  const failures = (evaluation.failures || []).map(item => esc(item.message)).join('；');
  return `<p>${badge(evaluation.passed ? 'ready' : 'failed', evaluation.passed ? '评估通过' : '评估拦截')}
    <small>对照在用 ${esc(evaluation.baseline_revision)} · ${date(evaluation.created_at)}${stale ? ' · 在用版本已变，旧评估需重算' : ''}</small></p>
    ${evaluation.passed ? `<small>${kilns}</small>` : `<p class="muted">${failures}</p>`}`;
}

function modelDesk() {
  const registry = state.models;
  if (!registry) return '';
  const supervisor = state.session.user.role === 'supervisor';
  const versions = registry.versions.map(version => {
    const evaluation = version.evaluation;
    const stale = evaluation && evaluation.baseline_revision !== registry.active;
    const actions = supervisor && version.status === 'candidate' ? `
      <form data-action="models/${esc(version.revision)}/evaluate" class="model-actions"><button class="secondary">对照新旧预测</button></form>
      ${evaluation && evaluation.passed && !stale ? `<details class="edit"><summary>发布该版本</summary>
        <form data-action="models/${esc(version.revision)}/publish" class="form-grid">
        ${field('切换依据', 'rationale', 'minlength="4" maxlength="400" placeholder="例如：已签核窑次对照误差未上升"')}
        <button>确认发布</button></form></details>` : ''}` : '';
    return `<article class="recipe-line"><div><b>${esc(version.revision)}</b> ${badge(...modelStatus[version.status])}</div>
      <p>${esc(version.note)}</p>
      <small>种子 ${version.config.seed} · L1 比例 ${version.config.l1_ratios.join(' / ')} · ${version.config.folds} 折交叉验证 · 登记 ${esc(version.created_by || '系统')} · ${date(version.created_at)}</small>
      ${evaluationCell(version, registry.active)}${actions}</article>`;
  }).join('');
  const rollbackable = registry.releases.some(release => release.kind === 'publish' && !release.rolled_back_at);
  const releases = registry.releases.length ? table(['时间', '类型', '版本切换', '切换依据', '操作人'],
    registry.releases.map(release => [date(release.decided_at), release.kind === 'rollback' ? '回滚' : '发布',
      `${esc(release.from_revision)} → ${esc(release.to_revision)}${release.rolled_back_at ? '<br><small>已回滚</small>' : ''}`,
      esc(release.rationale), esc(release.decided_by)])) : empty('尚未发生模型切换。');
  return `<section class="board"><div class="section-title"><h3>模型版本与切换</h3>
    ${registry.active ? `<span class="model-tag">在用 ${esc(registry.active)}</span>` : ''}</div>
    <p class="muted">发布前以同一批已签核窑次对照新旧预测：缺预测或任一窑号平均绝对误差上升即拦截；在用版本变化后旧评估需重算。在途试算沿用接单时版本，回滚不影响已签核结果。</p>
    ${versions}
    ${supervisor ? `<details class="edit"><summary>登记候选模型</summary><form data-action="models" class="form-grid">
      ${field('版本号', 'revision', 'pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,39}" placeholder="en-20261122"')}
      ${field('随机种子', 'seed', 'type="number" min="1" max="999999" value="2071"')}
      ${field('L1 比例候选（逗号分隔）', 'l1_ratios', 'value="0.15,0.5,0.85,1.0"')}
      ${field('交叉验证折数', 'folds', 'type="number" min="2" max="10" value="5"')}
      ${field('版本说明', 'note', 'minlength="4" maxlength="400" placeholder="调整依据与预期效果"')}
      <button>登记候选版本</button></form></details>
      ${rollbackable ? `<details class="edit"><summary>回滚最近发布</summary><form data-action="models/rollback" class="form-grid">
      ${field('回滚说明', 'note', 'minlength="4" maxlength="400"')}<button>回滚到上一版本</button></form></details>` : ''}` : ''}
    <details class="edit" open><summary>切换依据 · ${registry.releases.length} 条</summary>${releases}</details></section>`;
}

export function load(batch) {
  const desk = modelDesk();
  if (!batch) return empty('先在烧成批次登记窑次。') + desk;
  const run = batch.runs[0];
  const operator = state.session.user.role === 'operator';
  const supervisor = state.session.user.role === 'supervisor';
  let body;
  if (run?.status === 'ready') {
    const r = run.result;
    body = `<div class="analysis-grid">${showLoad(r)}<section class="model"><h4>弹性网络校验</h4><dl>
      <dt>模型版本</dt><dd>${esc(run.model_revision || r.model_revision || '—')}</dd><dt>留出集 MAE</dt><dd>${amount(r.mae_m3)} m³</dd>
      <dt>训练 / 留出</dt><dd>${r.train_count} / ${r.holdout_count}</dd>
      <dt>交叉验证</dt><dd>${r.cv_folds} 折</dd><dt>α / L1 比例</dt><dd>${r.alpha.toFixed(5)} / ${r.l1_ratio}</dd>
      <dt>随机种子</dt><dd>${r.seed}</dd></dl><p>MAE 来自合成历史批次。配方版本与节拍签核在周期复核中登记。</p></section></div>
      <details class="edit"><summary>模型特征与缩放后系数</summary>${table(['特征','本次输入','缩放后系数'],
        Object.entries(r.features).map(([key,value]) => [esc(key), amount(value), amount(r.coefs_scaled[key])]))}
        <p class="digest">训练集 SHA-256 <code>${esc(r.training_sha256)}</code></p></details>${readingTrace(run)}`;
  } else if (run?.status === 'queued') {
    body = `<p class="empty">试算 #${run.id} 已排队，页面会更新结果。</p><button type="button" data-refresh class="secondary">刷新试算状态</button>`;
  } else if (run?.status === 'failed') {
    body = `<p class="empty">试算 #${run.id} 失败（${esc(run.error)}）。主管可按原快照重试。</p>${supervisor ? `<form data-action="runs/${run.id}/retry"><button>按原快照重试</button></form>` : ''}${readingTrace(run)}`;
  } else {
    body = `<div class="coverage"><div><b>温区</b><span>${batch.curves.length} / 3 阶段</span><p>升温、保温、冷却必须连续覆盖烧成周期。</p></div>
      <div><b>累计表</b><span>${batch.meters.length} 条读数</span><p>进窑、出窑必须有记录，间隔不得超过三小时。</p></div></div>
      <p class="muted">待校验周期 ${clock(batch.opened_at)}–${clock(batch.closed_at)}。提交后冻结原始台账。</p>
      ${operator ? '<form data-action="runs"><button>校验并提交试算</button></form>' : ''}`;
  }
  return `${ticket(batch)}<section class="board"><div class="section-title"><h3>周期负荷参考</h3>
    ${run ? `<span class="model-tag">试算 #${run.id} · 模型 ${esc(run.model_revision || '—')}</span>` : ''}
    ${run ? badge(run.status, {ready:'试算完成',failed:'试算失败',queued:'队列等待'}[run.status]) : ''}</div>${body}</section>${desk}`;
}
