import { CHANNELS, STORAGE_KEY, demoSample, summarizeBaseline, summarizeDemo, recordsToCsv, parseRecords } from './core.mjs';
import { UNTRAINED_BADGE, TOAST_EVENTS, bpmView, connectLive, eventLabel, initialState, inputLabel, latestResult, normalize, noticeText, phaseDisplay, phaseStep, reduce, sessionLabel, signalHeading, streamStatusText, trialView, wsUrl } from './live.mjs';
const $ = id => document.getElementById(id);
const palette = { hr: '#4e8166', gsr: '#c68c58', fsr: '#9790c3' };
const questionBank = ['지금 떠올린 숫자는 짝수인가요?', '오늘 아침을 먹었나요?', '지금 보고 있는 카드에 숫자 3이 있나요?', '최근 일주일 안에 영화를 봤나요?', '지금 선택한 색은 파란색인가요?'];
let records = [];
let storageAvailable = true;
try { records = parseRecords(localStorage.getItem(STORAGE_KEY)); } catch { storageAvailable = false; }
let settings = { participant: 'P01', baselineDuration: 8, answerDuration: 6 };
let sessionId, phase = 'idle', baseline = null, baselineSamples = [], measured = [], stream = [], phaseStarted = 0, phaseLastTick = 0, latestId = null, round = 0, questionIndex = 0;
let toastTimer;
let live = null; // 기기 연동 모드 상태 (Python 서버 /ws에 연결되면 채워진다. null이면 기존 데모 모드)
const shortDate = date => new Intl.DateTimeFormat('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(date));
const signed = (n, digits = 1) => `${n >= 0 ? '+' : ''}${n.toFixed(digits)}`;
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4200); }
function saveRecords() {
  records = records.slice(0, 100);
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(records)); storageAvailable = true; }
  catch { storageAvailable = false; toast('브라우저 저장 공간을 사용할 수 없습니다. 기록을 내보내 주세요.'); }
  renderHistory(); renderRecent();
}
function setPage() {
  const requested = location.hash.slice(1);
  const page = ['dashboard', 'history', 'method'].includes(requested) ? requested : 'dashboard';
  for (const name of ['dashboard', 'history', 'method']) $(`${name}-page`).hidden = name !== page;
  document.querySelectorAll('[data-page]').forEach(link => { link.classList.toggle('active', link.dataset.page === page); if (link.dataset.page === page) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
  $('page-name').textContent = { dashboard: '라이브 세션', history: '측정 기록', method: '알고리즘 설계' }[page];
  document.title = `TruthPulse · ${$('page-name').textContent}`;
  if (page === 'dashboard') drawCharts();
}
function newSession(notify = true) {
  sessionId = crypto.randomUUID();
  phase = 'idle'; baseline = null; baselineSamples = []; measured = []; latestId = null; round = 0;
  $('session-label').textContent = `SESSION ${sessionId.slice(0, 6).toUpperCase()}`;
  $('participant-display').textContent = `참가자 ${settings.participant}`;
  $('result-content').replaceChildren();
  const placeholder = document.createElement('div'); placeholder.className = 'result-placeholder'; placeholder.textContent = '—';
  const msg = document.createElement('p'); msg.className = 'result-message'; msg.textContent = '질문을 마치면 변화 요약이 여기에 나타납니다.';
  $('result-content').append(placeholder, msg);
  $('label-controls').hidden = true;
  $('ground-truth').value = '';
  CHANNELS.forEach(key => $(`${key}-baseline`).textContent = '측정 전');
  renderPhase(); renderRecent();
  if (notify) toast('새 세션을 시작했습니다. 개인 기준을 다시 측정해 주세요.');
}
function renderPhase() {
  const active = phase === 'baseline' || phase === 'answer';
  $('control-title').textContent = { idle: '먼저, 나만의 기준부터.', baseline: '편안한 상태를 기록해요.', ready: '이제, 질문에 답해 보세요.', answer: '답변 속 변화를 읽고 있어요.', result: '다음 질문을 이어가 볼까요?' }[phase];
  $('control-description').textContent = { idle: '편안한 상태의 신호를 모아 이번 세션의 개인 기준을 만들어요.', baseline: '가상 신호의 평균과 변동 정도를 계산합니다. 첫 1초는 안정화 구간으로 제외해요.', ready: '질문을 고르고 측정을 시작하세요. 선택한 시연 패턴에 따라 가상 신호가 달라집니다.', answer: '정해진 시간 동안 가상 신호를 모으고, 개인 기준과 비교합니다.', result: '기준은 유지됩니다. 다른 패턴을 선택해 변화를 비교해 보세요.' }[phase];
  $('phase-pill').textContent = { idle: '준비 완료', baseline: '기준 측정 중', ready: '기준 설정 완료', answer: '답변 측정 중', result: '측정 완료' }[phase];
  $('calibration-visual').hidden = !!baseline;
  $('calibration-visual').classList.toggle('measuring', phase === 'baseline');
  $('question-fields').hidden = !baseline;
  for (const id of ['question', 'scenario', 'next-question', 'settings-open']) $(id).disabled = active;
  $('primary-action').disabled = active;
  $('primary-action').textContent = phase === 'baseline' ? '개인 기준 측정 중…' : phase === 'answer' ? '답변 측정 중…' : !baseline ? '개인 기준 측정 시작 ↗' : phase === 'result' ? '다음 질문 측정 시작 ↗' : '답변 측정 시작 ↗';
  $('cancel-action').hidden = !active;
  $('progress-label').textContent = baseline ? '데모 답변 측정' : '데모 기준 측정';
  $('progress-time').textContent = `${baseline ? settings.answerDuration : settings.baselineDuration}초`;
  $('phase-progress').value = phase === 'result' || phase === 'ready' ? 100 : 0;
  $('control-hint').textContent = baseline ? '정답 여부는 결과 확인 후 기록할 수 있습니다.' : '실제 측정 시간은 센서 실험 후 결정합니다.';
  const current = !baseline ? 'baseline' : phase === 'result' ? 'result' : 'question';
  ['baseline', 'question', 'result'].forEach((s, i) => { $(`step-${s}`).className = current === s ? 'current' : i < ['baseline', 'question', 'result'].indexOf(current) ? 'complete' : ''; });
  $('stream-status').textContent = active ? '가상 신호 기록 중' : '가상 신호 수신 중';
  $('chart-subtitle').textContent = phase === 'baseline' ? '개인 기준을 위한 안정 상태 구간을 기록하고 있어요.' : phase === 'answer' ? '현재 질문의 답변 구간을 기록하고 있어요.' : '센서별 변화를 같은 시간축에서 확인하세요.';
}
function startPhase() {
  if (phase === 'baseline' || phase === 'answer') return;
  if (baseline && !$('question').value.trim()) { $('question').focus(); toast('이번 질문을 입력해 주세요.'); return; }
  phase = baseline ? 'answer' : 'baseline'; measured = []; phaseStarted = performance.now(); phaseLastTick = phaseStarted;
  if (baseline) { latestId = null; $('label-controls').hidden = true; $('ground-truth').value = ''; $('result-content').replaceChildren(); const msg = document.createElement('p'); msg.className = 'result-message'; msg.textContent = '이번 답변을 측정하고 있습니다…'; $('result-content').append(msg); }
  renderPhase();
}
function cancelPhase(message = '측정을 취소했습니다. 다시 시작할 수 있습니다.') {
  if (!['baseline', 'answer'].includes(phase)) return;
  phase = baseline ? 'ready' : 'idle'; measured = []; renderPhase(); toast(message);
}
function finishPhase() {
  if (phase === 'baseline') {
    const result = summarizeBaseline(measured);
    if (!result.ok) { cancelPhase(result.reason); return; }
    baseline = result.baseline; baselineSamples = [...measured]; phase = 'ready';
    CHANNELS.forEach(key => $(`${key}-baseline`).textContent = `${baseline[key].mean.toFixed(key === 'hr' ? 1 : 2)}${key === 'hr' ? ' BPM' : ' a.u.'}`);
    renderPhase(); toast('개인 기준이 준비됐습니다. 이제 질문 측정을 시작하세요.');
  } else if (phase === 'answer') {
    const summary = summarizeDemo(measured, baseline);
    const record = { schemaVersion: 1, id: crypto.randomUUID(), sessionId, participant: settings.participant, createdAt: new Date().toISOString(), source: 'synthetic_demo', modelVersion: null, question: $('question').value.trim(), scenario: $('scenario').value, groundTruth: null, round: ++round, baseline, baselineSamples: [...baselineSamples], samples: [...measured], summary };
    records.unshift(record); latestId = record.id; phase = 'result'; renderPhase(); renderResult(record); saveRecords();
  }
}
function renderResult(record) {
  $('result-content').replaceChildren();
  const heading = document.createElement('div'); heading.className = `result-heading ${record.summary.kind === 'reactive' ? 'warn' : record.summary.kind === 'poor' ? 'poor' : ''}`; heading.textContent = record.summary.title;
  const reason = document.createElement('p'); reason.className = 'result-message'; reason.style.textAlign = 'left'; reason.textContent = record.summary.reason;
  $('result-content').append(heading, reason);
  if (record.summary.deltas) {
    const stats = document.createElement('div'); stats.className = 'result-deltas';
    CHANNELS.forEach(key => { const div = document.createElement('div'); div.textContent = { hr: '심박 변화', gsr: '피부 반응 변화', fsr: '압력 변화' }[key]; const strong = document.createElement('strong'); strong.textContent = `${signed(record.summary.deltas[key], key === 'hr' ? 1 : 2)} ${key === 'hr' ? 'BPM' : 'a.u.'}`; div.append(strong); stats.append(div); });
    $('result-content').append(stats);
  }
  $('label-controls').hidden = false;
}
function tick() {
  const now = performance.now();
  const active = ['baseline', 'answer'].includes(phase);
  if (active && now - phaseLastTick > 1500) { cancelPhase('측정이 잠시 중단되어 취소했습니다. 다시 측정해 주세요.'); }
  const duration = (phase === 'baseline' ? settings.baselineDuration : settings.answerDuration) * 1000;
  const elapsed = now - phaseStarted;
  const sample = demoSample(now, phase, $('scenario').value, Math.min(1, elapsed / duration));
  stream.push(sample); stream = stream.filter(s => now - s.t <= 20000);
  if (['baseline', 'answer'].includes(phase)) {
    phaseLastTick = now;
    if (phase !== 'baseline' || elapsed >= 1000) measured.push(sample);
    $('phase-progress').value = Math.min(100, elapsed / duration * 100);
    $('progress-time').textContent = `${Math.max(0, Math.ceil((duration - elapsed) / 1000))}초 남음`;
    if (elapsed >= duration) finishPhase();
  }
  $('hr-value').textContent = sample.hr.toFixed(0); $('gsr-value').textContent = sample.gsr.toFixed(2); $('fsr-value').textContent = sample.fsr.toFixed(2);
  if (sample.quality < 0.7) $('stream-status').textContent = '접촉 불량 시뮬레이션';
  drawCharts();
}
function setupCanvas(canvas) {
  const { width, height } = canvas.getBoundingClientRect();
  if (!width || !height) return null;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) { canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio); }
  const ctx = canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}
function trace(ctx, points, color, fillHeight = null) {
  if (!points.length) return;
  ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(...p) : ctx.moveTo(...p)); ctx.strokeStyle = color; ctx.lineWidth = 1.7; ctx.lineJoin = 'round'; ctx.stroke();
  if (fillHeight !== null) { ctx.lineTo(points.at(-1)[0], fillHeight); ctx.lineTo(points[0][0], fillHeight); ctx.closePath(); const gradient = ctx.createLinearGradient(0, 0, 0, fillHeight); gradient.addColorStop(0, color + '20'); gradient.addColorStop(1, color + '00'); ctx.fillStyle = gradient; ctx.fill(); }
}
function drawCharts() {
  if ($('dashboard-page').hidden) return;
  if (live) { drawLiveCharts(); return; }
  for (const key of CHANNELS) {
    const area = setupCanvas($(`${key}-spark`)); if (!area) continue;
    const { ctx, width, height } = area; const values = stream.slice(-45).map(s => s[key]);
    const center = baseline?.[key]?.mean ?? { hr: 72, gsr: 0.8, fsr: 0.45 }[key];
    const range = { hr: 20, gsr: 0.55, fsr: 0.4 }[key];
    trace(ctx, values.map((v, i) => [i / Math.max(values.length - 1, 1) * width, height - 4 - Math.max(0, Math.min(1, (v - center + range * 0.25) / range)) * (height - 8)]), palette[key], height);
  }
  const area = setupCanvas($('timeline')); if (!area) return;
  const { ctx, width, height } = area; const left = 28, top = 18, bottom = height - 27, right = width - 4;
  ctx.font = '9px sans-serif'; ctx.fillStyle = '#a2ac97'; ctx.lineWidth = 1;
  for (let i = 0; i < 4; i++) { const y = top + i / 3 * (bottom - top); ctx.beginPath(); ctx.strokeStyle = '#edf1e7'; ctx.setLineDash([3, 4]); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke(); ctx.fillText(String(Math.round(100 - i * 100 / 3)), 0, y + 3); }
  ctx.setLineDash([]);
  for (let i = 0; i <= 4; i++) ctx.fillText(i === 4 ? '지금' : `${20 - i * 5}초 전`, left + i / 4 * (right - left) - (i === 4 ? 17 : 0), height - 5);
  if (!stream.length) return;
  const end = stream.at(-1).t;
  const transforms = { hr: s => 0.52 + s.ppg * 0.23 + (s.hr - 72) / 100, gsr: s => 0.28 + (s.gsr - 0.8) * 0.8, fsr: s => 0.16 + (s.fsr - 0.45) * 1.1 };
  CHANNELS.forEach(key => trace(ctx, stream.map(s => [left + (1 - (end - s.t) / 20000) * (right - left), bottom - Math.max(0.02, Math.min(0.97, transforms[key](s))) * (bottom - top)]), palette[key]));
}
function recordButton(record) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'record-row';
  const info = document.createElement('span'); info.textContent = record.question;
  const meta = document.createElement('small'); meta.textContent = `${shortDate(record.createdAt)} · ${record.participant} · 가상 데이터`; info.append(meta);
  const result = document.createElement('span'); result.textContent = record.summary.title + ' ↗'; button.append(info, result); button.addEventListener('click', () => openDetail(record)); return button;
}
function renderRecent() {
  const current = records.filter(r => r.sessionId === sessionId).slice(0, 3);
  $('recent-rounds').replaceChildren();
  if (!current.length) { const box = document.createElement('div'); box.className = 'empty-inline'; const icon = document.createElement('span'); icon.textContent = '⌁'; const text = document.createElement('p'); text.textContent = '첫 번째 질문을 기다리고 있어요.'; const br = document.createElement('br'); const small = document.createElement('small'); small.textContent = '기준 측정 후 질문을 시작해 보세요.'; text.append(br, small); box.append(icon, text); $('recent-rounds').append(box); }
  current.forEach(r => $('recent-rounds').append(recordButton(r)));
}
function renderHistory() {
  const query = $('history-search').value.toLocaleLowerCase().trim();
  const filtered = records.filter(r => `${r.question} ${r.participant}`.toLocaleLowerCase().includes(query));
  $('record-count').textContent = records.length; $('history-total').textContent = `${filtered.length}개의 기록`;
  for (const id of ['export-csv', 'export-json', 'clear-history']) $(id).disabled = !records.length;
  $('history-content').replaceChildren();
  if (!filtered.length) { const empty = document.createElement('div'); empty.className = 'history-empty'; const title = document.createElement('h2'); title.textContent = query ? '검색 결과가 없어요.' : '아직 측정 기록이 없어요.'; const text = document.createElement('p'); text.textContent = query ? '다른 질문이나 참가자 별칭으로 검색해 보세요.' : '라이브 세션에서 개인 기준을 측정하고 첫 질문에 답해 보세요.'; const link = document.createElement('a'); link.href = '#dashboard'; link.className = 'button secondary'; link.textContent = '라이브 세션으로'; empty.append(title, text, link); $('history-content').append(empty); return; }
  const table = document.createElement('table'); table.className = 'history-table';
  const thead = document.createElement('thead'); const headRow = document.createElement('tr'); ['일시 / 참가자', '질문', '변화 요약', '실험 정답', ''].forEach(label => { const th = document.createElement('th'); th.scope = 'col'; th.textContent = label; headRow.append(th); }); thead.append(headRow); table.append(thead);
  const tbody = document.createElement('tbody');
  filtered.forEach(record => { const row = document.createElement('tr'); const date = document.createElement('td'); date.textContent = shortDate(record.createdAt); const name = document.createElement('div'); name.className = 'row-label'; name.textContent = record.participant; date.append(name); const q = document.createElement('td'); q.className = 'question-cell'; q.textContent = record.question; const result = document.createElement('td'); result.textContent = record.summary.title; const label = document.createElement('td'); label.textContent = record.groundTruth === null ? '미표기' : record.groundTruth === 0 ? '진실 · 0' : '거짓 · 1'; const action = document.createElement('td'); const button = document.createElement('button'); button.className = 'button secondary'; button.textContent = '상세'; button.setAttribute('aria-label', `${record.question} 기록 상세`); button.addEventListener('click', () => openDetail(record)); action.append(button); row.append(date, q, result, label, action); tbody.append(row); });
  table.append(tbody); $('history-content').append(table);
}
function openDetail(record) {
  $('detail-content').replaceChildren();
  const question = document.createElement('p'); question.className = 'detail-question'; question.textContent = record.question;
  const meta = document.createElement('p'); meta.textContent = `${record.participant} · ${shortDate(record.createdAt)} · 가상 데이터`;
  const list = document.createElement('dl'); list.className = 'detail-list';
  const entries = [['변화 요약', record.summary.title], ['실험 정답', record.groundTruth === null ? '미표기' : record.groundTruth === 0 ? '진실 · 0' : '거짓 · 1'], ['답변 샘플 수', `${record.samples.length}개`], ['기준 샘플 수', `${record.baselineSamples.length}개`], ['학습된 모델', '미연결']];
  if (record.summary.deltas) CHANNELS.forEach(key => entries.push([{ hr: '심박 변화', gsr: '피부 반응 변화', fsr: '압력 변화' }[key], `${signed(record.summary.deltas[key], key === 'hr' ? 1 : 2)} ${key === 'hr' ? 'BPM' : 'a.u.'}`]));
  entries.forEach(([term, val]) => { const dt = document.createElement('dt'); dt.textContent = term; const dd = document.createElement('dd'); dd.textContent = val; list.append(dt, dd); });
  const note = document.createElement('p'); note.textContent = record.summary.reason;
  $('detail-content').append(question, meta, list, note); $('detail-dialog').showModal();
}
function download(content, type, suffix) {
  const now = new Date(); const localDate = [now.getFullYear(), String(now.getMonth() + 1).padStart(2, '0'), String(now.getDate()).padStart(2, '0')].join('-');
  const url = URL.createObjectURL(new Blob([content], { type })); const a = document.createElement('a'); a.href = url; a.download = `truthpulse-demo-${localDate}.${suffix}`; document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast('가상 측정 기록을 내보냈습니다.');
}
$('primary-action').addEventListener('click', startPhase);
$('cancel-action').addEventListener('click', () => cancelPhase());
$('new-session').addEventListener('click', () => newSession());
$('next-question').addEventListener('click', () => { questionIndex = (questionIndex + 1) % questionBank.length; $('question').value = questionBank[questionIndex]; });
$('ground-truth').addEventListener('change', () => { const record = records.find(r => r.id === latestId); if (!record) return; record.groundTruth = $('ground-truth').value === '' ? null : Number($('ground-truth').value); saveRecords(); toast('실험 정답을 기록했습니다. 변화 요약은 그대로 유지됩니다.'); });
$('history-search').addEventListener('input', renderHistory);
$('export-csv').addEventListener('click', () => download(recordsToCsv(records), 'text/csv;charset=utf-8', 'csv'));
$('export-json').addEventListener('click', () => download(JSON.stringify({ schemaVersion: 1, source: 'synthetic_demo', warning: '가상 데이터입니다. 실제 학습·검증에 사용하지 마세요.', units: { t: 'monotonic ms within browser page', hr: 'BPM', ppg: 'a.u.', gsr: 'a.u.', fsr: 'a.u.' }, modelVersion: null, records }, null, 2), 'application/json', 'json'));
$('settings-open').addEventListener('click', () => { $('participant').value = settings.participant; $('baseline-duration').value = settings.baselineDuration; $('answer-duration').value = settings.answerDuration; $('settings-dialog').showModal(); });
$('settings-close').addEventListener('click', () => $('settings-dialog').close());
$('settings-form').addEventListener('submit', e => { e.preventDefault(); const participant = $('participant').value.trim(); if (!participant) { $('participant').focus(); return; } settings = { participant, baselineDuration: Number($('baseline-duration').value), answerDuration: Number($('answer-duration').value) }; $('settings-dialog').close(); newSession(); });
$('clear-history').addEventListener('click', () => $('clear-dialog').showModal());
$('clear-cancel').addEventListener('click', () => $('clear-dialog').close());
$('clear-confirm').addEventListener('click', () => { records = []; latestId = null; $('label-controls').hidden = true; saveRecords(); $('clear-dialog').close(); toast('데모 기록을 모두 삭제했습니다.'); });
$('detail-close').addEventListener('click', () => $('detail-dialog').close());
window.addEventListener('hashchange', setPage); window.addEventListener('resize', drawCharts);
document.addEventListener('visibilitychange', () => { if (document.hidden && ['baseline', 'answer'].includes(phase)) cancelPhase('화면을 벗어나 측정을 중단했습니다. 다시 측정해 주세요.'); });
newSession(false); renderHistory(); setPage();
const initial = performance.now();
for (let t = Math.max(0, initial - 20000); t < initial; t += 200) stream.push(demoSample(t));
tick(); const demoTimer = setInterval(tick, 200);
if (!storageAvailable) toast('기록 저장 공간을 사용할 수 없습니다. 내보내기 기능을 이용해 주세요.');

// ---------- 기기 연동 모드 (Python 서버의 /ws, 보기 전용) ----------
// /ws에 처음 연결되면 데모 공급(demoSample, summarizeDemo)을 멈추고 서버가 보내는 단계와 결과만 표시한다.
// 연결할 수 없으면(예전 데모 서버 npm run dev) 기존 데모 모드를 그대로 쓴다.
let connection = 'connecting', liveDrawPending = false, renderedResultKey = null;
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
function enterLiveMode() {
  clearInterval(demoTimer);
  phase = 'idle'; stream = [];
  for (const id of ['primary-action', 'cancel-action', 'question-fields', 'calibration-visual', 'progress-info', 'phase-progress', 'new-session', 'label-controls']) $(id).hidden = true;
  $('settings-open').disabled = true;
  $('control-hint').textContent = '진행은 기기 버튼으로 합니다. 이 화면은 보기 전용입니다.';
  $('device-note-title').textContent = '기기 주도 · 보기 전용';
  $('device-note-body').replaceChildren('웹에서는 기기를 조작하지 않습니다.', document.createElement('br'), '진행과 종료는 기기 버튼으로 합니다.');
  $('hr-foot-label').textContent = '심박'; renderLiveBpm(null);
  $('hr-spark').setAttribute('aria-label', 'PPG 적외선 원신호 최근 변화 그래프');
  $('gsr-spark').setAttribute('aria-label', '피부 반응 원신호 최근 변화 그래프');
  $('fsr-spark').setAttribute('aria-label', '손가락 압력 원신호 최근 변화 그래프');
  $('timeline').setAttribute('aria-label', 'PPG, GSR, FSR 원신호의 최근 20초 타임라인. 채널마다 따로 정규화한 상대 크기입니다.');
  $('chart-subtitle').textContent = '서버가 보낸 원신호를 채널마다 따로 정규화해 같은 시간축에 그립니다.';
  renderedResultKey = null;
}
function renderLiveStatus() {
  const s = live.status;
  $('mode-pill-text').textContent = inputLabel(s) + (s?.finished ? ' · 끝남' : '');
  const [label, body] = noticeText(s);
  $('notice-text').replaceChildren(el('strong', '', label), ` ${body} 판정은 오락용이며 거짓의 증명이 아닙니다.`);
  $('signal-heading').textContent = signalHeading(s);
  $('stream-status').textContent = streamStatusText(s, connection === 'open' ? 'open' : 'disconnected');
  const banner = $('connection-banner');
  if (connection !== 'open') { banner.hidden = false; banner.className = 'connection-banner warn'; banner.textContent = '서버와 연결이 끊겼습니다. 자동으로 다시 연결하는 중입니다…'; }
  else if (live.error) { banner.hidden = false; banner.className = 'connection-banner warn'; banner.textContent = live.error.message; }
  else banner.hidden = true;
}
function renderLivePhase() {
  const p = live.phase?.phase;
  const shown = phaseDisplay(p, live.status); // 재생/더미의 손 올리기 단계는 알약을 숨기고 '따라갑니다' 문구
  $('phase-pill').hidden = !shown.pill; $('phase-pill').textContent = shown.pill ?? '';
  $('control-title').textContent = shown.title; $('control-description').textContent = shown.desc;
  const steps = ['baseline', 'question', 'result'], current = phaseStep(p);
  steps.forEach((s, i) => { $(`step-${s}`).className = current === s ? 'current' : i < steps.indexOf(current) ? 'complete' : ''; });
  $('session-label').textContent = sessionLabel(live.session);
  $('participant-display').textContent = live.phase?.trial ? `질문 ${live.phase.trial}` : '질문 전';
  const bl = live.baseline;
  for (const k of ['gsr', 'fsr']) {
    const m = bl?.[k]?.mean, sd = bl?.[k]?.sd;
    $(`${k}-baseline`).textContent = Number.isFinite(m) ? `${m.toFixed(0)}${Number.isFinite(sd) ? ` ± ${sd.toFixed(1)}` : ''}` : '측정 전';
  }
}
function renderLiveBpm(msg) {
  const v = bpmView(msg); // 값이 없을 때만 '심박 계산 전'
  $('hr-value').textContent = v.value; $('hr-unit').textContent = v.unit; $('hr-baseline').textContent = v.foot;
}
function renderLiveValues() {
  const last = live.samples.at(-1);
  $('gsr-value').textContent = last ? String(last.gsr) : '—';
  $('fsr-value').textContent = last ? String(last.fsr) : '—';
}
function renderLiveResult() {
  const r = latestResult(live);
  const key = r ? `${live.results.length}|${trialView(r).key}` : null;
  if (key === renderedResultKey) return; // 같은 결과를 다시 그리지 않는다 (aria-live 반복 낭독 방지)
  renderedResultKey = key;
  const box = $('result-content'); box.replaceChildren();
  if (!r) {
    $('result-pill').textContent = UNTRAINED_BADGE;
    box.append(el('div', 'result-placeholder', '—'), el('p', 'result-message', '기기 단계를 따라가는 중입니다. 답변이 끝나면 판정과 이유가 여기에 나타납니다.'));
    return;
  }
  const v = trialView(r);
  $('result-pill').textContent = v.badge ?? v.judgeLabel;
  const head = el('div', 'live-decision');
  head.append(el('div', `result-heading ${v.kind}`, v.decisionLabel));
  if (v.badge) head.append(el('span', 'badge-untrained', v.badge));
  box.append(el('p', 'live-meta', `${v.title} · ${v.sourceLabel} · 판정기 ${v.judgeLabel}`), head);
  box.append(el('p', 'live-label', v.decision === 'inconclusive' ? '보류 사유' : '판정 이유'));
  const list = el('ul', 'reason-list'); v.reasons.forEach(t => list.append(el('li', '', t))); box.append(list);
  if (v.contributions.length) {
    const bars = el('div', 'contrib-list');
    v.contributions.forEach(c => {
      const row = el('div', `contrib-row${c.over ? ' over' : ''}`);
      const bar = el('span', 'contrib-bar'); const fill = el('i'); fill.style.width = `${c.width}%`; bar.append(fill);
      row.append(el('span', 'contrib-label', c.label), bar, el('span', 'contrib-value', c.text));
      bars.append(row);
    });
    box.append(el('p', 'live-label', '센서별 변화 (가운데 선 = 임계값)'), bars);
  }
  if (v.warnings.length) box.append(el('p', 'field-note', `경고: ${v.warnings.join(', ')}`));
  box.append(el('p', 'field-note live-judge-note', v.judgeNote), el('p', 'field-note live-disclaimer', v.disclaimer));
}
function renderLiveRecent() {
  const rows = live.results.slice(-3).reverse();
  $('recent-rounds').replaceChildren();
  if (!rows.length) { const box = el('div', 'empty-inline'); box.append(el('span', '', '⌁'), el('p', '', '첫 판정을 기다리고 있어요.')); $('recent-rounds').append(box); return; }
  rows.forEach(r => { const v = trialView(r); const row = el('div', 'record-row'); const info = el('span', '', v.title); info.append(el('small', '', `${v.sourceLabel}${v.badge ? ` · ${v.badge}` : ''}`)); row.append(info, el('span', '', v.decisionLabel)); $('recent-rounds').append(row); });
}
function renderLive() { renderLiveStatus(); renderLivePhase(); renderLiveValues(); renderLiveBpm(live.bpm); renderLiveResult(); renderLiveRecent(); scheduleLiveDraw(); }
function scheduleLiveDraw() {
  if (liveDrawPending) return;
  liveDrawPending = true;
  requestAnimationFrame(() => { liveDrawPending = false; drawCharts(); });
}
function drawLiveCharts() {
  const pts = live.samples;
  const field = { hr: 'ir', gsr: 'gsr', fsr: 'fsr' };
  for (const key of CHANNELS) {
    const area = setupCanvas($(`${key}-spark`)); if (!area) continue;
    const { ctx, width, height } = area; const recent = pts.slice(-125); // 약 5초 (25Hz)
    const ys = normalize(recent.map(p => p[field[key]]));
    trace(ctx, ys.map((y, i) => [i / Math.max(ys.length - 1, 1) * width, height - 4 - y * (height - 8)]), palette[key], height);
  }
  const area = setupCanvas($('timeline')); if (!area) return;
  const { ctx, width, height } = area; const left = 28, top = 18, bottom = height - 27, right = width - 4;
  ctx.font = '9px sans-serif'; ctx.fillStyle = '#a2ac97'; ctx.lineWidth = 1;
  for (let i = 0; i < 4; i++) { const y = top + i / 3 * (bottom - top); ctx.beginPath(); ctx.strokeStyle = '#edf1e7'; ctx.setLineDash([3, 4]); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke(); }
  ctx.setLineDash([]);
  for (let i = 0; i <= 4; i++) ctx.fillText(i === 4 ? '최근' : `${20 - i * 5}초 전`, left + i / 4 * (right - left) - (i === 4 ? 17 : 0), height - 5);
  if (!pts.length) return;
  const end = pts.at(-1).t;
  const bands = { hr: [0.55, 0.95], gsr: [0.30, 0.60], fsr: [0.05, 0.35] };
  CHANNELS.forEach(key => {
    const ys = normalize(pts.map(p => p[field[key]])); const [lo, hi] = bands[key];
    trace(ctx, pts.map((p, i) => [left + (1 - (end - p.t) / 20000) * (right - left), bottom - (lo + ys[i] * (hi - lo)) * (bottom - top)]), palette[key]);
  });
}
if (location.protocol.startsWith('http')) {
  connectLive({
    url: wsUrl(location),
    onState(state, info) {
      if (state === 'unavailable') return; // /ws가 없다: 데모 모드 유지
      connection = state;
      if (state === 'open') {
        if (!live) enterLiveMode();
        live = initialState(); renderedResultKey = null; // 서버가 상태와 기록을 다시 보내 준다
        if (info?.reconnected) toast('서버에 다시 연결됐습니다.');
      }
      if (live) renderLive();
    },
    onMessage(msg) {
      if (!live) return;
      live = reduce(live, msg);
      if (msg.type === 'sample_batch') { renderLiveValues(); scheduleLiveDraw(); return; }
      if (msg.type === 'bpm_update') { renderLiveBpm(live.bpm); return; }
      if (msg.type === 'phase_update' && msg.event && TOAST_EVENTS.has(msg.event.name)) toast(eventLabel(msg.event));
      if (msg.type === 'error') toast(msg.message);
      renderLive();
    },
  });
}
