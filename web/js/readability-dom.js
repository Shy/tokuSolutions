import {
  imageWhitespaceSafety,
  layoutReadabilityPage,
  rolesForPage
} from './readability-layout.js?v=22';

export function fitReadabilityPage({ element, page, pageIndex, probe, measureProbeText, scale }) {
  const image = element.querySelector('img');
  const rectangle = image.getBoundingClientRect();
  const width = rectangle.width,
    height = rectangle.height;
  element.querySelectorAll('.readability-mask').forEach((node) => node.remove());
  element.classList.add('readability-page');
  const blocks = rolesForPage(page);
  let isSafe;
  try {
    isSafe = imageWhitespaceSafety(image, page, blocks);
  } catch {
    isSafe = () => false;
  } // A failed pixel read must never grant expansion.
  probe.style.wordBreak = 'normal';
  probe.style.overflowWrap = 'normal';
  const textCanvas = document.createElement('canvas').getContext('2d');
  const wordCache = new Map(),
    measureCache = new Map();
  const measure = (text, fontSize, availableWidth) => {
    fontSize = Math.max(0.25, Math.floor(fontSize * 4) / 4);
    const key = JSON.stringify([text, fontSize, availableWidth]);
    if (measureCache.has(key)) return measureCache.get(key);
    const actual = measureProbeText(probe, text, {
      fontSize,
      lineHeight: 1.15,
      width: availableWidth,
      whiteSpace: 'pre-line'
    });
    const wordKey = JSON.stringify([text, fontSize]);
    if (!wordCache.has(wordKey)) {
      textCanvas.font = `${fontSize}px ${getComputedStyle(probe).fontFamily}`;
      wordCache.set(
        wordKey,
        Math.max(0, ...text.split(/\s+/).map((word) => textCanvas.measureText(word).width))
      );
    }
    const result = { ...actual, wordWidth: wordCache.get(wordKey) };
    measureCache.set(key, result);
    return result;
  };
  const layout = layoutReadabilityPage({ blocks, width, height, measure, isSafe, scale });
  const results = [];
  layout.forEach((placement, index) => {
    const overlay = element.querySelector(`.overlay[data-projection-index="${index}"]`);
    const box = placement.bbox;
    overlay.dataset.placement = placement.placement;
    overlay.dataset.role = placement.role;
    overlay.dataset.fitStatus = placement.status;
    overlay.style.cssText += `;left:${box[0] * width}px;top:${box[1] * height}px;width:${box[2] * width}px;height:${box[3] * height}px;font-size:var(--readability-font-size);padding:${placement.padding}px;border-width:0`;
    overlay.style.setProperty('--readability-font-size', `${placement.fontSize}px`);
    const original = blocks[index].bbox;
    if (
      box[0] > original[0] ||
      box[1] > original[1] ||
      box[0] + box[2] < original[0] + original[2] ||
      box[1] + box[3] < original[1] + original[3]
    ) {
      const mask = document.createElement('div');
      mask.className = 'readability-mask';
      mask.style.cssText = `position:absolute;left:${original[0] * width}px;top:${original[1] * height}px;width:${original[2] * width}px;height:${original[3] * height}px;background:#fff;pointer-events:none;display:${overlay.style.display === 'none' ? 'none' : 'block'}`;
      element.insertBefore(mask, element.querySelector('.overlay'));
    }
    results.push({
      pageIndex,
      blockIndex: blocks[index].blockIndex,
      status: placement.status,
      constrained: placement.constrained,
      fontSize: placement.fontSize,
      placement: placement.placement
    });
  });
  probe.style.wordBreak = '';
  probe.style.overflowWrap = '';
  return results;
}
