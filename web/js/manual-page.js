import { displayBlocksForPage } from './readability-layout.js?v=22';
// The reader and PDF capture share this DOM, stylesheet and text-fitting path.
export function createManualPage(page, pageIndex, imageSrc, { correctedTextVisible = true } = {}) {
  const element = document.createElement('div');
  element.className = 'page';
  element.id = `page-${pageIndex}`;
  const image = document.createElement('img');
  image.alt = `Page ${pageIndex + 1}`;
  image.src = imageSrc;
  element.appendChild(image);

  for (const [projectionIndex, block] of displayBlocksForPage(page).entries()) {
    const blockIndex = block.blockIndex;
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    if (block.layout === 'panel') overlay.classList.add('semantic-panel');
    overlay.textContent = block.translation;
    overlay.dataset.pageId = pageIndex;
    overlay.dataset.blockId = blockIndex;
    overlay.dataset.projectionIndex = projectionIndex;
    [overlay.style.left, overlay.style.top, overlay.style.width, overlay.style.height] =
      block.bbox.map((value) => `${value * 100}%`);
    if (!correctedTextVisible) overlay.style.display = 'none';
    element.appendChild(overlay);
  }

  const label = document.createElement('div');
  label.className = 'page-label';
  label.textContent = `Page ${pageIndex + 1}`;
  element.appendChild(label);
  return element;
}
