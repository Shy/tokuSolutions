import { copyFile, mkdir } from 'node:fs/promises';

await mkdir('web/vendor', { recursive: true });
for (const [source, target] of [
  ['html-to-image/dist/html-to-image.js', 'html-to-image.js'],
  ['html-to-image/LICENSE', 'html-to-image.LICENSE'],
  ['jspdf/dist/jspdf.umd.min.js', 'jspdf.umd.min.js'],
  ['jspdf/LICENSE', 'jspdf.LICENSE']
]) {
  await copyFile(`node_modules/${source}`, `web/vendor/${target}`);
}
