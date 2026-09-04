import { createHash } from 'node:crypto';
import { access, mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, '..');
const defaultResourceRoot = path.join(projectRoot, 'android', 'app', 'src', 'main', 'res');
const defaultSource = path.join(projectRoot, 'assets', 'branding', 'icon-candidates', '04-continuous-path-foreground.png');
const sourcePath = path.resolve(projectRoot, argumentValue('--source') ?? defaultSource);
const resourceRoot = path.resolve(projectRoot, argumentValue('--resource-root') ?? defaultResourceRoot);
const defaultManifestPath = path.join(projectRoot, 'assets', 'branding', 'branding-manifest.json');
const manifestPath = path.resolve(projectRoot, argumentValue('--manifest') ?? defaultManifestPath);
assertProjectPath(resourceRoot, 'Resource root');
assertProjectPath(manifestPath, 'Manifest path');
if (resourceRoot === projectRoot) throw new Error('Resource root cannot be the project root.');

const densityScales = {
  mdpi: 1,
  hdpi: 1.5,
  xhdpi: 2,
  xxhdpi: 3,
  xxxhdpi: 4,
};

const backgroundHex = '#FFFFFF';
const background = { r: 255, g: 255, b: 255, alpha: 1 };
const transparent = { r: 0, g: 0, b: 0, alpha: 0 };
const alphaNoiseCutoff = 4;

await access(sourcePath);
const sourceMetadata = await sharp(sourcePath).metadata();
if (sourceMetadata.format !== 'png') throw new Error('Branding source must be a PNG.');
if ((sourceMetadata.width ?? 0) < 1024 || (sourceMetadata.height ?? 0) < 1024) {
  throw new Error('Branding source must be at least 1024 x 1024 pixels.');
}
if (sourceMetadata.width !== sourceMetadata.height) throw new Error('Branding source must be square.');
if (!sourceMetadata.hasAlpha) throw new Error('Branding source must contain a transparent alpha channel.');
const sourceStats = await sharp(sourcePath).ensureAlpha().stats();
if (sourceStats.channels[3].min === 255) throw new Error('Branding source alpha channel is fully opaque.');

const denoisedSource = await removeLowAlphaNoise(sourcePath);
const normalizedSource = await sharp(denoisedSource)
  .trim({ background: transparent })
  .png()
  .toBuffer();

const normalizedMetadata = await sharp(normalizedSource).metadata();
if ((normalizedMetadata.width ?? 0) < 64 || (normalizedMetadata.height ?? 0) < 64) {
  throw new Error('Branding source has no usable visible artwork.');
}

const generatedFiles = [];
for (const [density, scale] of Object.entries(densityScales)) {
  const legacySize = Math.round(48 * scale);
  const adaptiveSize = Math.round(108 * scale);
  const directory = path.join(resourceRoot, `mipmap-${density}`);
  await mkdir(directory, { recursive: true });

  const adaptive = await transparentArtwork(adaptiveSize, 0.68);
  const monochrome = await sharp({
    create: { width: adaptiveSize, height: adaptiveSize, channels: 4, background: { r: 255, g: 255, b: 255, alpha: 1 } },
  })
    .composite([{ input: adaptive, blend: 'dest-in' }])
    .png()
    .toBuffer();
  const legacyArtwork = await transparentArtwork(legacySize, 0.78);
  const legacyBase = await sharp({ create: { width: legacySize, height: legacySize, channels: 4, background } })
    .composite([{ input: legacyArtwork, gravity: 'centre' }])
    .png()
    .toBuffer();
  const rounded = await applyMask(legacyBase, roundedRectangleMask(legacySize));
  const circular = await applyMask(legacyBase, circleMask(legacySize));

  await writeGenerated(path.join(directory, 'ic_launcher_foreground.png'), adaptive);
  await writeGenerated(path.join(directory, 'ic_launcher_monochrome.png'), monochrome);
  await writeGenerated(path.join(directory, 'ic_launcher.png'), rounded);
  await writeGenerated(path.join(directory, 'ic_launcher_round.png'), circular);
}

const anyDpiDirectory = path.join(resourceRoot, 'mipmap-anydpi');
await mkdir(anyDpiDirectory, { recursive: true });
const adaptiveIcon = `<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background" />
    <foreground android:drawable="@mipmap/ic_launcher_foreground" />
    <monochrome android:drawable="@mipmap/ic_launcher_monochrome" />
</adaptive-icon>
`;
await writeGenerated(path.join(anyDpiDirectory, 'ic_launcher.xml'), Buffer.from(adaptiveIcon));
await writeGenerated(path.join(anyDpiDirectory, 'ic_launcher_round.xml'), Buffer.from(adaptiveIcon));

const valuesDirectory = path.join(resourceRoot, 'values');
const backgroundResource = `<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">${backgroundHex}</color>
</resources>
`;
await writeGenerated(path.join(valuesDirectory, 'ic_launcher_background.xml'), Buffer.from(backgroundResource));

await removeLegacyBrandingResources();

const manifest = {
  source: path.relative(projectRoot, sourcePath).replaceAll('\\', '/'),
  sourceSha256: await sha256(sourcePath),
  sourceDimensions: `${sourceMetadata.width}x${sourceMetadata.height}`,
  generator: `sharp ${sharp.versions.sharp}`,
  background: backgroundHex,
  outputs: [],
};
for (const file of generatedFiles.sort()) {
  manifest.outputs.push({
    path: path.relative(projectRoot, file).replaceAll('\\', '/'),
    sha256: await sha256(file),
  });
}
await mkdir(path.dirname(manifestPath), { recursive: true });
await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');

console.log(`Branding source: ${sourcePath}`);
console.log(`Generated resources: ${generatedFiles.length}`);
console.log(`Manifest: ${manifestPath}`);

function argumentValue(name) {
  const index = process.argv.indexOf(name);
  if (index === -1) return null;
  if (!process.argv[index + 1] || process.argv[index + 1].startsWith('--')) {
    throw new Error(`${name} requires a value.`);
  }
  return process.argv[index + 1];
}

function assertProjectPath(target, label) {
  const relative = path.relative(projectRoot, target);
  if (relative === '' || relative.startsWith(`..${path.sep}`) || relative === '..' || path.isAbsolute(relative)) {
    throw new Error(`${label} must stay inside the project directory.`);
  }
}

async function transparentArtwork(canvasSize, ratio) {
  const artworkSize = Math.round(canvasSize * ratio);
  const artwork = await sharp(normalizedSource)
    .resize(artworkSize, artworkSize, { fit: 'contain', background: transparent, withoutEnlargement: false })
    .png()
    .toBuffer();
  return sharp({ create: { width: canvasSize, height: canvasSize, channels: 4, background: transparent } })
    .composite([{ input: artwork, gravity: 'centre' }])
    .png()
    .toBuffer();
}

async function removeLowAlphaNoise(input) {
  const { data, info } = await sharp(input).rotate().ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  for (let offset = 3; offset < data.length; offset += 4) {
    if (data[offset] <= alphaNoiseCutoff) data[offset] = 0;
  }
  return sharp(data, { raw: info }).png().toBuffer();
}

async function applyMask(input, mask) {
  return sharp(input).composite([{ input: mask, blend: 'dest-in' }]).png().toBuffer();
}

function roundedRectangleMask(size) {
  const radius = Math.round(size * 0.22);
  return Buffer.from(`<svg width="${size}" height="${size}"><rect width="${size}" height="${size}" rx="${radius}" fill="#fff" /></svg>`);
}

function circleMask(size) {
  const radius = size / 2;
  return Buffer.from(`<svg width="${size}" height="${size}"><circle cx="${radius}" cy="${radius}" r="${radius}" fill="#fff" /></svg>`);
}

async function writeGenerated(destination, contents) {
  await mkdir(path.dirname(destination), { recursive: true });
  await writeFile(destination, contents);
  generatedFiles.push(destination);
}

async function removeLegacyBrandingResources() {
  const entries = await readdir(resourceRoot, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isDirectory() || !entry.name.startsWith('drawable')) continue;
    const splashPath = path.join(resourceRoot, entry.name, 'splash.png');
    if (await exists(splashPath)) await rm(splashPath);
  }
  await rm(path.join(resourceRoot, 'mipmap-anydpi-v26'), { recursive: true, force: true });
  await rm(path.join(resourceRoot, 'drawable-v24', 'ic_launcher_foreground.xml'), { force: true });
  await rm(path.join(resourceRoot, 'drawable', 'ic_launcher_background.xml'), { force: true });
  await rm(path.join(resourceRoot, 'drawable', 'splash.xml'), { force: true });
}

async function exists(file) {
  try {
    await stat(file);
    return true;
  } catch (error) {
    if (error?.code === 'ENOENT') return false;
    throw error;
  }
}

async function sha256(file) {
  const bytes = await readFile(file);
  return createHash('sha256').update(bytes).digest('hex');
}
