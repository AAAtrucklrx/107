import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, '..');
const brandingRoot = path.join(projectRoot, 'assets', 'branding');
const sourceSvgPath = path.join(brandingRoot, 'xiaowo-mark-v4.svg');
const sourcePngPath = path.join(brandingRoot, 'xiaowo-mark-v4.png');
const previewPath = path.join(brandingRoot, 'xiaowo-icon-v4-preview.png');
const canvasSize = 2048;
const previewSize = 1024;
const background = '#0756A6';

await mkdir(brandingRoot, { recursive: true });
const sourceSvg = await readFile(sourceSvgPath);
const sourcePng = await sharp(sourceSvg, { density: 192 })
  .resize(canvasSize, canvasSize, { fit: 'fill' })
  .png()
  .toBuffer();
await writeFile(sourcePngPath, sourcePng);

const previewMark = await sharp(sourcePng)
  .resize(previewSize, previewSize, { fit: 'fill' })
  .png()
  .toBuffer();
const preview = await sharp({
  create: {
    width: previewSize,
    height: previewSize,
    channels: 4,
    background,
  },
})
  .composite([{ input: previewMark }])
  .png()
  .toBuffer();
await writeFile(previewPath, preview);

console.log(`Selected mark PNG: ${sourcePngPath}`);
console.log(`Selected icon preview: ${previewPath}`);
