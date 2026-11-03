export const CHANNELS = ['hr', 'gsr', 'fsr'];
export const STORAGE_KEY = 'truthpulse.demo.records.v1';
export const mean = values => values.reduce((a, b) => a + b, 0) / values.length;
export const deviation = values => { const m = mean(values); return Math.sqrt(mean(values.map(v => (v - m) ** 2))); };
export function validSample(s) {
  return !!s && [s.t, s.hr, s.ppg, s.gsr, s.fsr, s.quality].every(Number.isFinite)
    && s.t >= 0 && s.hr > 0 && s.quality >= 0 && s.quality <= 1;
}
export function checkSamples(samples) {
  if (!Array.isArray(samples) || samples.length < 20) return { ok: false, reason: '측정 데이터가 부족합니다.' };
  if (samples.some(s => !validSample(s))) return { ok: false, reason: '유효하지 않은 신호가 있습니다.' };
  if (samples.some((s, i) => i && (s.t <= samples[i - 1].t || s.t - samples[i - 1].t > 1000))) return { ok: false, reason: '신호가 끊겼습니다. 다시 측정해 주세요.' };
  if (samples.filter(s => s.quality < 0.7).length / samples.length > 0.1) return { ok: false, reason: '접촉 상태가 불안정합니다. 다시 측정해 주세요.' };
  return { ok: true };
}
export function summarizeBaseline(samples) {
  const quality = checkSamples(samples);
  if (!quality.ok) return quality;
  const baseline = {};
  for (const key of CHANNELS) {
    const values = samples.map(s => s[key]);
    baseline[key] = { mean: mean(values), sd: deviation(values) };
    if (baseline[key].sd < 0.00001) return { ok: false, reason: '변화가 없는 신호가 있습니다. 센서 상태를 확인해 주세요.' };
  }
  return { ok: true, baseline };
}
// Demo-only descriptive statistics. This is not a trained classifier and has no lie probability.
export function summarizeDemo(samples, baseline) {
  const quality = checkSamples(samples);
  if (!quality.ok) return { kind: 'poor', title: '측정 보류', reason: quality.reason, deltas: null, score: null, probability: null };
  if (!baseline || CHANNELS.some(k => !baseline[k] || !Number.isFinite(baseline[k].mean) || !(baseline[k].sd > 0))) throw new Error('개인 기준이 필요합니다.');
  const deltas = Object.fromEntries(CHANNELS.map(key => [key, mean(samples.map(s => s[key])) - baseline[key].mean]));
  // Floors and threshold below only control this synthetic demonstration, not the experiment.
  const floors = { hr: 1, gsr: 0.04, fsr: 0.04 };
  const score = mean(CHANNELS.map(key => Math.abs(deltas[key]) / Math.max(baseline[key].sd, floors[key])));
  const kind = score >= 2 ? 'reactive' : 'calm';
  return { kind, title: kind === 'reactive' ? '평소보다 큰 변화' : '기준과 비슷한 변화', reason: '가상 신호의 변화 요약입니다. 진실·거짓 판정이나 거짓 확률을 의미하지 않습니다.', deltas, score, probability: null };
}
export function demoSample(t, phase = 'idle', scenario = 'calm', progress = 0) {
  const time = t / 1000;
  const response = phase === 'answer' && scenario === 'reactive' ? Math.sin(Math.min(1, progress) * Math.PI / 2) : 0;
  const poor = phase === 'answer' && scenario === 'poor';
  const hr = 72 + Math.sin(time * 0.7) * 1.2 + Math.sin(time * 2.1) * 0.3 + response * 13;
  const angle = (time * 1.2) % 1;
  const ppg = Math.exp(-(((angle - 0.22) / 0.07) ** 2)) + 0.25 * Math.exp(-(((angle - 0.48) / 0.12) ** 2));
  return { t, hr, ppg, gsr: 0.8 + Math.sin(time * 0.6) * 0.025 + response * 0.35, fsr: 0.45 + Math.sin(time * 1.4) * 0.025 + response * 0.22, quality: poor ? 0.25 : 0.99 };
}
export function csvCell(value) {
  let text = String(value ?? '');
  // Escape spreadsheet formulas even after leading whitespace or control characters.
  if (/^[\s\u0000-\u001f]*[=+@-]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}
export function recordsToCsv(records) {
  const fields = ['record_id', 'session_id', 'participant_id', 'recorded_at', 'source', 'question', 'ground_truth', 'demo_pattern', 'summary', 'hr_delta_bpm', 'gsr_delta_au', 'fsr_delta_au', 'model_version'];
  const rows = records.map(r => [r.id, r.sessionId, r.participant, r.createdAt, 'synthetic_demo', r.question, r.groundTruth ?? '', r.scenario, r.summary.title, r.summary.deltas?.hr ?? '', r.summary.deltas?.gsr ?? '', r.summary.deltas?.fsr ?? '', 'none']);
  return '\uFEFF' + [fields, ...rows].map(row => row.map(csvCell).join(',')).join('\r\n');
}
export function validRecord(r) {
  return !!r && r.schemaVersion === 1 && r.source === 'synthetic_demo' && typeof r.id === 'string' && typeof r.sessionId === 'string'
    && typeof r.question === 'string' && typeof r.participant === 'string' && typeof r.createdAt === 'string' && Number.isFinite(Date.parse(r.createdAt))
    && ['calm', 'reactive', 'poor'].includes(r.scenario) && [null, 0, 1].includes(r.groundTruth)
    && !!r.summary && ['calm', 'reactive', 'poor'].includes(r.summary.kind) && typeof r.summary.title === 'string'
    && typeof r.summary.reason === 'string' && (r.summary.deltas === null || CHANNELS.every(k => Number.isFinite(r.summary.deltas?.[k])))
    && Array.isArray(r.samples) && r.samples.length <= 1000 && r.samples.every(validSample)
    && Array.isArray(r.baselineSamples) && r.baselineSamples.length <= 1000 && r.baselineSamples.every(validSample);
}
export function parseRecords(text) {
  try { const records = JSON.parse(text); return Array.isArray(records) ? records.filter(validRecord).slice(0, 100) : []; }
  catch { return []; }
}
