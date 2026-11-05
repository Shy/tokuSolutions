import { classifyPrintOrientation } from './print-preparation.js?v=17';
import { fitOverlayText, measureProbeText } from './text-fit-dom.js?v=17';

const SHEET_PIXELS = Object.freeze({
  portrait: { width: 733, height: 1062 },
  landscape: { width: 1062, height: 733 }
});

export function buildPrintPageModels(manual, assets) {
  return manual.pages.map((page, pageIndex) => {
    const asset = assets[pageIndex];
    const orientation = classifyPrintOrientation(asset);
    const sheet = SHEET_PIXELS[orientation];
    const scale = Math.min(sheet.width / asset.width, sheet.height / asset.height);
    return {
      pageIndex,
      orientation,
      pageName: `manual-${orientation}`,
      imageSrc: asset.src,
      sourceSize: { width: asset.width, height: asset.height },
      contentSize: {
        width: asset.width * scale,
        height: asset.height * scale
      },
      blocks: page.blocks
        .map((block, blockIndex) => ({
          translation: block.translation,
          bbox: [...block.bbox],
          ...(block.layout === 'panel' ? { layout: 'panel' } : {}),
          blockIndex
        }))
        .filter((block) => block.translation.trim() !== ''),
      missing: page.blocks
        .map((block, blockIndex) => ({ block, blockIndex }))
        .filter(({ block }) => block.translation.trim() === '')
        .map(({ blockIndex }) => blockIndex)
    };
  });
}

export function renderPrintDocument(manual, assets) {
  const models = buildPrintPageModels(manual, assets);
  const root = document.createElement('div');
  root.id = 'printRoot';
  root.className = 'print-root';

  for (const model of models) {
    const sheet = document.createElement('section');
    sheet.className = `print-sheet print-sheet--${model.orientation}`;
    sheet.style.page = model.pageName;
    sheet.dataset.pageIndex = String(model.pageIndex);

    const content = document.createElement('div');
    content.className = 'print-page-content';
    content.style.width = `${model.contentSize.width}px`;
    content.style.height = `${model.contentSize.height}px`;

    const image = document.createElement('img');
    image.src = model.imageSrc;
    image.alt = '';
    content.appendChild(image);

    for (const block of model.blocks) {
      const overlay = document.createElement('div');
      overlay.className = 'print-overlay';
      if (block.layout === 'panel') overlay.classList.add('semantic-panel');
      overlay.dataset.blockIndex = String(block.blockIndex);
      overlay.textContent = block.translation;
      [overlay.style.left, overlay.style.top, overlay.style.width, overlay.style.height] =
        block.bbox.map((value) => `${value * 100}%`);
      content.appendChild(overlay);
    }

    sheet.appendChild(content);
    root.appendChild(sheet);
  }

  document.body.appendChild(root);
  return {
    root,
    models,
    cleanup() {
      root.remove();
    }
  };
}

export async function fitPrintDocument(representation) {
  const probe = document.createElement('div');
  probe.className = 'print-fit-probe';
  representation.root.appendChild(probe);
  const results = [];

  try {
    for (const model of representation.models) {
      for (const blockIndex of model.missing) {
        results.push({ pageIndex: model.pageIndex, blockIndex, status: 'missing' });
      }

      const sheet = representation.root.querySelector(
        `.print-sheet[data-page-index="${model.pageIndex}"]`
      );
      for (const block of model.blocks) {
        const overlay = sheet.querySelector(
          `.print-overlay[data-block-index="${block.blockIndex}"]`
        );
        const result = fitOverlayText({
          text: block.translation,
          bbox: block.bbox,
          sourceSize: model.sourceSize,
          renderedSize: model.contentSize,
          padding: 2,
          minFontSize: 12,
          maxFontSize: 36,
          lineHeight: 1.15,
          measure: (text, style) =>
            measureProbeText(probe, text, {
              ...style,
              whiteSpace: getComputedStyle(overlay).whiteSpace
            })
        });
        overlay.dataset.fitStatus = result.status;
        overlay.style.fontSize = `${result.fontSize}px`;
        results.push({ pageIndex: model.pageIndex, blockIndex: block.blockIndex, ...result });
      }
    }
  } finally {
    probe.remove();
  }

  return results;
}
