import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveBuild, checkApkSize } from './build-config.mjs';

test('debug defaults to USB loopback API and accepts only loopback HTTP', () => {
  assert.equal(resolveBuild('debug').apiBase, 'http://127.0.0.1:8000/api/v1');
  assert.throws(() => resolveBuild('debug', 'http://114.214.241.119:8850/api/v1'));
  assert.throws(() => resolveBuild('debug', 'http://127.0.0.1.evil.test/api/v1'));
  assert.throws(() => resolveBuild('debug', 'http://localhost:8000/api/v1'));
  assert.throws(() => resolveBuild('debug', 'http://[::1]:8000/api/v1'));
  assert.throws(() => resolveBuild('debug', 'http://127.0.0.1:8001/api/v1'));
});
test('release requires explicit HTTPS endpoint without credentials or query', () => {
  assert.throws(() => resolveBuild('release'));
  assert.throws(() => resolveBuild('release', 'http://127.0.0.1:8000/api/v1'));
  assert.throws(() => resolveBuild('release', 'https://name:secret@example.org/api/v1'));
  assert.throws(() => resolveBuild('release', 'https://example.org/api/v1?token=secret'));
  assert.throws(() => resolveBuild('release', 'https://example.org/'));
  assert.throws(() => resolveBuild('release', 'https://example.org/prefix/api/v1'));
  assert.equal(resolveBuild('release', 'https://xiaowo.example.org/api/v1/').apiBase,
    'https://xiaowo.example.org/api/v1');
});
test('no implicit build mode or oversized APK may be delivered', () => {
  assert.throws(() => resolveBuild('production', 'https://example.org/api/v1'));
  assert.doesNotThrow(() => checkApkSize(25_000_000));
  assert.throws(() => checkApkSize(25_000_001));
  assert.throws(() => checkApkSize(0));
});
