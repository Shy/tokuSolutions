import { createManualPage } from './manual-page.js?v=18';
import { fitRenderedManual, waitForFitFont } from './text-fit-dom.js?v=18';

// Executed inside a real 1280×900 browsing context, including on phones.
window.renderPdfPage = async (page, manualName, preferences) => {
  const root = document.getElementById('pagePanel');
  const imageSrc = new URL(
    `../manuals/${encodeURIComponent(manualName)}/${page.image}`,
    location.href
  );
  const element = createManualPage(page, 0, imageSrc.href, preferences);
  root.replaceChildren(element);
  await element.querySelector('img').decode();
  await waitForFitFont();
  if (!document.fonts.check('16px "Source Sans 3"')) throw new Error('Reader font unavailable');
  await fitRenderedManual({
    root,
    manual: { pages: [page] },
    generation: 0,
    isCurrentGeneration: () => true,
    readerScale: preferences.scale
  });
  // This is reader chrome, not part of the source manual page.
  element.querySelector('.page-label').remove();
  return element;
};
