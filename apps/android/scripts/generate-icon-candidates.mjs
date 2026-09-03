import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, '..');
const outputRoot = path.join(projectRoot, 'assets', 'branding', 'icon-candidates');
await mkdir(outputRoot, { recursive: true });

const spiral = 'M278 596C278 421 420 279 595 279C746 279 868 401 868 552C868 679 765 782 638 782C535 782 452 699 452 596C452 517 516 453 595 453C662 453 716 507 716 574C716 629 671 674 616 674';
const candidates = [
  {
    id: '01-orbit', label: '轨道螺旋',
    svg: icon('#062A50', `
      <circle cx="512" cy="512" r="376" fill="#0A3764" stroke="#DDFBFF" stroke-width="20"/>
      <path d="${spiral}" fill="none" stroke="#55DFE8" stroke-width="62" stroke-linecap="round"/>
      <path d="M226 812H798" stroke="#FFFFFF" stroke-width="48" stroke-linecap="round"/>
      <circle cx="805" cy="211" r="36" fill="#FFFFFF"/>
    `),
  },
  {
    id: '02-editorial', label: '学术印记',
    svg: icon('#F4F0E7', `
      <rect x="160" y="160" width="704" height="704" rx="168" fill="#FFFFFF" stroke="#034EA1" stroke-width="28"/>
      <path d="${spiral}" fill="none" stroke="#034EA1" stroke-width="54" stroke-linecap="square"/>
      <path d="M224 808H800" stroke="#099BA8" stroke-width="42"/>
      <path d="M222 230H374M650 230H802" stroke="#099BA8" stroke-width="24"/>
    `),
  },
  {
    id: '03-knowledge-nodes', label: '知识节点',
    svg: icon('#071B35', `
      <path d="${spiral}" fill="none" stroke="#8EF3F2" stroke-width="34" stroke-linecap="round"/>
      <path d="M225 798H805" stroke="#3E8BFF" stroke-width="38" stroke-linecap="round"/>
      <g fill="#FFFFFF" stroke="#3E8BFF" stroke-width="18">
        <circle cx="278" cy="596" r="42"/><circle cx="595" cy="279" r="42"/><circle cx="868" cy="552" r="42"/>
        <circle cx="452" cy="596" r="34"/><circle cx="716" cy="574" r="34"/><circle cx="616" cy="674" r="34"/>
      </g>
    `),
  },
  {
    id: '04-continuous-path', label: '一笔路径',
    svg: icon('#FFFFFF', `
      <path d="M190 804H834M278 596C278 421 420 279 595 279C746 279 868 401 868 552C868 679 765 782 638 782C535 782 452 699 452 596C452 517 516 453 595 453C662 453 716 507 716 574C716 629 671 674 616 674" fill="none" stroke="#034EA1" stroke-width="52" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M204 270L294 360M820 270L730 360" stroke="#099BA8" stroke-width="34" stroke-linecap="round"/>
    `),
  },
  {
    id: '05-archive-grid', label: '档案网格',
    svg: icon('#034EA1', `
      <g fill="#DDFBFF"><rect x="164" y="164" width="218" height="218"/><rect x="642" y="164" width="218" height="218"/><rect x="164" y="642" width="218" height="218"/><rect x="642" y="642" width="218" height="218"/></g>
      <path d="${spiral}" fill="none" stroke="#55DFE8" stroke-width="48" stroke-linecap="square"/>
      <path d="M196 814H828" stroke="#FFFFFF" stroke-width="44"/>
    `),
  },
  {
    id: '06-folded-card', label: '折页知识',
    svg: icon('#E9F7F7', `
      <path d="M174 184H696L850 338V840H174Z" fill="#FFFFFF" stroke="#034EA1" stroke-width="26" stroke-linejoin="round"/>
      <path d="M696 184V338H850" fill="#8FE2E3" stroke="#034EA1" stroke-width="26" stroke-linejoin="round"/>
      <path d="${spiral}" transform="translate(-18 36) scale(.92)" fill="none" stroke="#099BA8" stroke-width="58" stroke-linecap="round"/>
      <path d="M242 790H750" stroke="#034EA1" stroke-width="38"/>
    `),
  },
  {
    id: '07-night-orbit', label: '深空轨道',
    svg: icon('#030D22', `
      <circle cx="512" cy="512" r="354" fill="#092D60" stroke="#285DB3" stroke-width="22"/>
      <circle cx="512" cy="512" r="414" fill="none" stroke="#55DFE8" stroke-width="14" stroke-dasharray="28 34"/>
      <path d="${spiral}" fill="none" stroke="#FFFFFF" stroke-width="48" stroke-linecap="round"/>
      <path d="M232 812H792" stroke="#55DFE8" stroke-width="44" stroke-linecap="round"/>
      <circle cx="839" cy="292" r="28" fill="#55DFE8"/>
    `),
  },
  {
    id: '08-soft-shell', label: '柔和蜗壳',
    svg: icon('#DFF7F4', `
      <circle cx="556" cy="526" r="326" fill="#FFFFFF" stroke="#0B6E79" stroke-width="24"/>
      <path d="${spiral}" transform="translate(-26 -18) scale(.96)" fill="none" stroke="#0B6E79" stroke-width="66" stroke-linecap="round"/>
      <path d="M214 790C346 732 650 740 820 804" fill="none" stroke="#034EA1" stroke-width="50" stroke-linecap="round"/>
      <circle cx="266" cy="290" r="30" fill="#034EA1"/><circle cx="756" cy="240" r="30" fill="#034EA1"/>
    `),
  },
  {
    id: '09-negative-space', label: '负形书页',
    svg: icon('#034EA1', `
      <path d="M144 196C278 154 408 182 512 266C616 182 746 154 880 196V826C746 784 616 812 512 896C408 812 278 784 144 826Z" fill="#FFFFFF"/>
      <path d="M512 266V896" stroke="#55AFC1" stroke-width="26"/>
      <path d="${spiral}" transform="translate(-56 0) scale(.92)" fill="none" stroke="#034EA1" stroke-width="54" stroke-linecap="round"/>
      <path d="M226 798H750" stroke="#099BA8" stroke-width="40"/>
    `),
  },
  {
    id: '10-campus-contours', label: '校园等高线',
    svg: icon('#F7FAFC', `
      <g fill="none" stroke="#9AD7DD" stroke-width="24">
        <circle cx="512" cy="512" r="394"/><circle cx="512" cy="512" r="330"/><circle cx="512" cy="512" r="266"/>
      </g>
      <path d="${spiral}" fill="none" stroke="#034EA1" stroke-width="54" stroke-linecap="round"/>
      <path d="M210 812H814" stroke="#099BA8" stroke-width="46" stroke-linecap="round"/>
      <path d="M206 274L286 354M818 274L738 354" stroke="#034EA1" stroke-width="28" stroke-linecap="round"/>
    `),
  },
];

const tiles = [];
for (let index = 0; index < candidates.length; index += 1) {
  const candidate = candidates[index];
  const svgPath = path.join(outputRoot, `${candidate.id}.svg`);
  const pngPath = path.join(outputRoot, `${candidate.id}.png`);
  await writeFile(svgPath, candidate.svg, 'utf8');
  const png = await sharp(Buffer.from(candidate.svg)).resize(1024, 1024).png().toBuffer();
  await writeFile(pngPath, png);
  tiles.push({ input: await makeTile(png, index + 1, candidate.label), left: (index % 5) * 440 + 40, top: Math.floor(index / 5) * 520 + 120 });
}

const boardWidth = 2280;
const boardHeight = 1200;
const board = await sharp({ create: { width: boardWidth, height: boardHeight, channels: 4, background: '#ECF1F5' } })
  .composite([
    { input: Buffer.from(`<svg width="${boardWidth}" height="${boardHeight}"><text x="48" y="66" font-family="Arial, sans-serif" font-size="36" font-weight="700" fill="#132231">小蜗 Android 图标候选 · 01—10</text><text x="48" y="100" font-family="Arial, sans-serif" font-size="20" fill="#536473">基于 107 主项目螺旋轨迹字标；无校徽、无文字、适配圆形与圆角方形裁切</text></svg>`) },
    ...tiles,
  ])
  .png()
  .toBuffer();
await writeFile(path.join(outputRoot, 'contact-sheet.png'), board);
console.log(`Generated ${candidates.length} SVG/PNG candidates in ${outputRoot}`);

function icon(background, content) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><rect width="1024" height="1024" rx="224" fill="${background}"/>${content}</svg>`;
}

async function makeTile(png, number, label) {
  return sharp({ create: { width: 400, height: 480, channels: 4, background: '#FFFFFF' } })
    .composite([
      { input: await sharp(png).resize(360, 360).png().toBuffer(), left: 20, top: 20 },
      { input: Buffer.from(`<svg width="400" height="100"><text x="24" y="45" font-family="Arial, sans-serif" font-size="26" font-weight="700" fill="#034EA1">${String(number).padStart(2, '0')}</text><text x="78" y="45" font-family="Microsoft YaHei, Arial, sans-serif" font-size="24" fill="#132231">${label}</text><text x="24" y="78" font-family="Arial, sans-serif" font-size="16" fill="#657583">1024 × 1024 · SVG + PNG</text></svg>`), left: 0, top: 380 },
    ])
    .png()
    .toBuffer();
}
