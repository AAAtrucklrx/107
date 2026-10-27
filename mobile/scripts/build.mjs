#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { resolveBuild, checkApkSize } from './build-config.mjs';

const mobile = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = resolve(mobile, '..');
const config = resolveBuild(process.argv[2], process.env.XIAOWO_MOBILE_API_BASE);
const env = { ...process.env, VITE_MOBILE_API_BASE: config.apiBase, VITE_MOBILE_DEBUG: config.debug ? '1' : '0', CI: process.env.CI || '1' };
function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
function validateAssets(path) {
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    const file = join(path, entry.name);
    if (entry.isSymbolicLink()) throw new Error('Symlinks are not allowed in bundled web assets.');
    if (entry.isDirectory()) validateAssets(file);
    else if (/(^\.env|\.(db|sqlite|sqlite3|pem|key|p12|jks|py|pt|onnx)$)/i.test(entry.name)) {
      throw new Error(`Unexpected private/backend asset: ${entry.name}`);
    }
  }
}
run('npm', ['run', `build:mobile-${config.mode}`], join(root, 'frontend'));
const dist = join(root, 'frontend/dist');
if (!existsSync(join(dist, 'index.html'))) throw new Error('Frontend index.html missing.');
validateAssets(dist);
rmSync(join(mobile, 'www'), { recursive: true, force: true });
cpSync(dist, join(mobile, 'www'), { recursive: true });
writeFileSync(join(mobile, 'www/mobile-build.json'), JSON.stringify({ mode: config.mode, apiBase: config.apiBase }) + '\n');
run('npx', ['--no-install', 'cap', 'sync', 'android'], mobile);
if (process.argv.includes('--sync-only')) process.exit(0);
run('./gradlew', ['--no-daemon', config.debug ? 'assembleDebug' : 'assembleRelease'], join(mobile, 'android'));
const apkDir = join(mobile, 'android/app/build/outputs/apk', config.mode);
const apkName = readdirSync(apkDir).find(name => name.endsWith('.apk'));
if (!apkName) throw new Error('Gradle did not produce an APK.');
const apk = join(apkDir, apkName);
const size = statSync(apk).size;
checkApkSize(size);
const outputDir = join(mobile, 'artifacts');
mkdirSync(outputDir, { recursive: true });
const output = join(outputDir, `xiaowo-${config.mode}${apkName.includes('unsigned') ? '-unsigned' : ''}.apk`);
cpSync(apk, output);
const sha256 = createHash('sha256').update(readFileSync(output)).digest('hex');
writeFileSync(`${output}.sha256`, `${sha256}  ${output.split('/').pop()}\n`);
console.log(`APK: ${output}\nSize: ${size} bytes (${(size / 1_000_000).toFixed(2)} MB)\nSHA-256: ${sha256}`);
if (apkName.includes('unsigned')) console.log('Release APK is unsigned; configure signing before distribution.');
