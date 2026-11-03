import test from 'node:test';
import assert from 'node:assert/strict';
import { demoSample, summarizeBaseline, summarizeDemo, checkSamples, recordsToCsv, parseRecords } from '../dist/core.mjs';
const series = (phase = 'baseline', scenario = 'calm') => Array.from({ length: 40 }, (_, i) => demoSample(2000 + i * 200, phase, scenario, i / 39));
const baseline = summarizeBaseline(series()).baseline;
test('baseline needs enough valid data and refuses flat channels', () => {
  assert.equal(summarizeBaseline(series().slice(0, 10)).ok, false);
  assert.equal(summarizeBaseline(series().map(s => ({ ...s, gsr: 1 }))).ok, false);
  assert.equal(summarizeBaseline(series()).ok, true);
});
test('missing, reordered, and interrupted samples are rejected', () => {
  assert.equal(checkSamples(series().map((s, i) => i === 5 ? { ...s, hr: NaN } : s)).ok, false);
  assert.equal(checkSamples(series().reverse()).ok, false);
  assert.equal(checkSamples(series().map((s, i) => ({ ...s, t: s.t + (i > 20 ? 3000 : 0) }))).ok, false);
});
test('poor contact cannot produce a descriptive result or probability', () => {
  const result = summarizeDemo(series('answer', 'poor'), baseline);
  assert.equal(result.kind, 'poor'); assert.equal(result.deltas, null); assert.equal(result.probability, null);
});
test('simulated responses only produce change summaries, no lie probabilities', () => {
  const calm = summarizeDemo(series('answer', 'calm'), baseline);
  const reactive = summarizeDemo(series('answer', 'reactive'), baseline);
  assert.equal(calm.kind, 'calm'); assert.equal(reactive.kind, 'reactive');
  assert.equal(reactive.probability, null); assert.ok(reactive.deltas.hr > 5);
});
test('ground truth never influences descriptive statistics', () => {
  const data = series('answer', 'reactive');
  assert.deepEqual(summarizeDemo(data.map(s => ({ ...s, groundTruth: 0 })), baseline), summarizeDemo(data.map(s => ({ ...s, groundTruth: 1 })), baseline));
});
test('CSV escapes formulas, quotes, and multiline questions and marks synthetic data', () => {
  const csv = recordsToCsv([{ id: 'id', sessionId: 's', participant: '  =1+1', createdAt: '2026-01-01', question: 'A,"B"\nC', groundTruth: 0, scenario: 'calm', summary: { title: 'small', deltas: { hr: -2, gsr: 0.1, fsr: 0 } } }]);
  assert.ok(csv.includes('"\'  =1+1"')); assert.ok(csv.includes('"A,""B""\nC"')); assert.ok(csv.includes('synthetic_demo')); assert.ok(csv.includes('"0"'));
});
test('corrupt or incompatible stored records do not crash the application', () => {
  assert.deepEqual(parseRecords('{broken'), []); assert.deepEqual(parseRecords('{}'), []); assert.deepEqual(parseRecords('[null,{},1]'), []);
});
