import { fitText } from './text-fit.js?v=14';

export const READER_FIT_DEFAULTS = Object.freeze({
  padding: 2,
  lineHeight: 1.15,
  minFontSize: 8,
  maxFontSize: 28
});

export function calculateFitGeometry({
  bbox,
  sourceWidth,
  sourceHeight,
  renderedWidth,
  renderedHeight,
  padding,
  borderWidth = 0
}) {
  return {
    width: Math.max(0, bbox[2] * renderedWidth - (padding + borderWidth) * 2),
    height: Math.max(0, bbox[3] * renderedHeight - (padding + borderWidth) * 2),
    scaleX: renderedWidth / sourceWidth,
    scaleY: renderedHeight / sourceHeight
  };
}

export function fitOverlayText({
  text,
  bbox,
  sourceSize,
  renderedSize,
  padding = READER_FIT_DEFAULTS.padding,
  borderWidth = 0,
  minFontSize = READER_FIT_DEFAULTS.minFontSize,
  maxFontSize = READER_FIT_DEFAULTS.maxFontSize,
  lineHeight = READER_FIT_DEFAULTS.lineHeight,
  measure,
  generation,
  isCurrentGeneration
}) {
  const geometry = calculateFitGeometry({
    bbox,
    sourceWidth: sourceSize.width,
    sourceHeight: sourceSize.height,
    renderedWidth: renderedSize.width,
    renderedHeight: renderedSize.height,
    padding,
    borderWidth
  });

  return fitText({
    text,
    box: geometry,
    minFontSize,
    maxFontSize,
    generation,
    isCurrentGeneration,
    measure: (fontSize) => measure(text, { fontSize, lineHeight, width: geometry.width })
  });
}

function createMeasurementProbe() {
  const probe = document.createElement('div');
  probe.className = 'text-fit-probe';
  probe.setAttribute('aria-hidden', 'true');
  document.body.appendChild(probe);
  return probe;
}

export function measureProbeText(probe, text, { fontSize, lineHeight, width }) {
  probe.textContent = text;
  probe.style.width = `${width}px`;
  probe.style.fontSize = `${fontSize}px`;
  probe.style.lineHeight = String(lineHeight);
  // scrollWidth/scrollHeight are integer-valued. Comparing them with a
  // fractional OCR box can force overflow even when the text actually fits.
  const range = probe.ownerDocument.createRange();
  range.selectNodeContents(probe);
  return {
    width: range.getBoundingClientRect().width,
    height: probe.getBoundingClientRect().height
  };
}

export async function waitForFitFont() {
  if (!document.fonts) return;
  await document.fonts.load('16px "Source Sans 3"');
  await document.fonts.ready;
}

export async function fitRenderedManual({
  root,
  manual,
  generation,
  isCurrentGeneration,
  readerScale = 1
}) {
  await waitForFitFont();
  if (!isCurrentGeneration(generation)) return [];

  const probe = createMeasurementProbe();
  const results = [];

  try {
    for (const overlay of root.querySelectorAll('.overlay')) {
      const pageIndex = Number(overlay.dataset.pageId);
      const blockIndex = Number(overlay.dataset.blockId);
      const block = manual.pages[pageIndex].blocks[blockIndex];
      const image = overlay.closest('.page')?.querySelector('img');
      if (!image?.naturalWidth || !image?.naturalHeight) continue;

      const rendered = image.getBoundingClientRect();
      const result = fitOverlayText({
        text: block.translation,
        bbox: block.bbox,
        sourceSize: { width: image.naturalWidth, height: image.naturalHeight },
        renderedSize: { width: rendered.width, height: rendered.height },
        borderWidth: Number.parseFloat(getComputedStyle(overlay).borderLeftWidth) || 0,
        minFontSize: READER_FIT_DEFAULTS.minFontSize * readerScale,
        maxFontSize: READER_FIT_DEFAULTS.maxFontSize * readerScale,
        generation,
        isCurrentGeneration,
        measure: (text, style) => measureProbeText(probe, text, style)
      });

      if (result.status === 'stale') return results;
      overlay.dataset.fitStatus = result.status;
      overlay.style.fontSize = result.fontSize === null ? '' : `${result.fontSize}px`;
      overlay.classList.toggle('hidden', result.status === 'missing');
      results.push({ pageIndex, blockIndex, ...result });
    }
  } finally {
    probe.remove();
  }

  return results;
}
