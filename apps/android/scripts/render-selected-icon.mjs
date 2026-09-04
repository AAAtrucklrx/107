import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, '..');
const candidateRoot = path.join(projectRoot, 'assets', 'branding', 'icon-candidates');
const sourceSvgPath = path.join(candidateRoot, '04-continuous-path-foreground.svg');
const sourcePngPath = path.join(candidateRoot, '04-continuous-path-foreground.png');
const canvasSize = 2048;

await mkdir(candidateRoot, { recursive: true });
const sourceSvg = await readFile(sourceSvgPath);
const sourcePng = await sharp(sourceSvg, { density: 192 })
  .resize(canvasSize, canvasSize, { fit: 'fill' })
  .png()
  .toBuffer();
await writeFile(sourcePngPath, sourcePng);

console.log(`Selected candidate foreground: ${sourcePngPath}`);
