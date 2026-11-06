export const PDF_DESKTOP_VIEWPORT = Object.freeze({ width: 1280, height: 900 });
export const PDF_PIXEL_RATIO = 2;

function preserveMeasuredStyle(style, property, value) {
  // Custom-property token streams retain precision that ordinary CSSOM number
  // serialization drops (for example 121.90625px becoming 121.906px).
  const variable = `--pdf-${property}`;
  style.setProperty(variable, value);
  style.setProperty(property, `var(${variable})`);
}

export async function createDesktopCapture() {
  const frame = document.createElement('iframe');
  frame.title = 'Preparing manual download';
  frame.setAttribute('aria-hidden', 'true');
  frame.style.cssText = `position:fixed;left:-100000px;top:0;width:${PDF_DESKTOP_VIEWPORT.width}px;height:${PDF_DESKTOP_VIEWPORT.height}px;border:0;pointer-events:none`;
  try {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('PDF renderer timed out')), 30000);
      frame.onload = () => {
        clearTimeout(timeout);
        if (typeof frame.contentWindow.renderPdfPage !== 'function') {
          reject(new Error('PDF renderer failed to load'));
        } else resolve();
      };
      frame.onerror = () => {
        clearTimeout(timeout);
        reject(new Error('PDF renderer unavailable'));
      };
      frame.src = new URL('../pdf-frame.html?v=22', import.meta.url).href;
      document.body.appendChild(frame);
    });
    return {
      frame,
      renderPage: (...args) => frame.contentWindow.renderPdfPage(...args),
      cleanup: () => frame.remove()
    };
  } catch (error) {
    frame.remove();
    throw error;
  }
}

export async function downloadManualPdf({
  manual,
  manualName,
  preferences,
  onProgress = () => {}
}) {
  // Freeze the current editor document and reader controls at the click.
  const snapshot = structuredClone(manual);
  const reader = {
    scale: preferences.scale ?? 1,
    correctedTextVisible: preferences.correctedTextVisible ?? true
  };
  if (!snapshot.pages.length) throw new Error('No manual pages to download');
  await Promise.all([import('../vendor/html-to-image.js'), import('../vendor/jspdf.umd.min.js')]);
  const capture = await createDesktopCapture();
  try {
    let pdf;
    let fontEmbedCSS;
    for (const [index, page] of snapshot.pages.entries()) {
      onProgress(`Preparing PDF page ${index + 1} of ${snapshot.pages.length}…`);
      const element = await capture.renderPage(page, manualName, reader);
      const { width, height } = element.getBoundingClientRect();
      fontEmbedCSS ??= await globalThis.htmlToImage.getFontEmbedCSS(element);
      const capturedSvg = await globalThis.htmlToImage.toSvg(element, {
        width,
        height,
        pixelRatio: PDF_PIXEL_RATIO,
        fontEmbedCSS,
        backgroundColor: '#fff',
        style: { margin: '0', boxShadow: 'none', transition: 'none' }
      });
      // CSSOM serializes dimensions to limited precision. Preserve the actual
      // reader rectangles and fitted sizes so threshold-width lines cannot wrap.
      const svgDocument = new DOMParser().parseFromString(
        decodeURIComponent(capturedSvg.slice(capturedSvg.indexOf(',') + 1)),
        'image/svg+xml'
      );
      const clone = svgDocument.querySelector('foreignObject').firstElementChild;
      const origin = element.getBoundingClientRect();
      preserveMeasuredStyle(clone.style, 'height', `${height}px`);
      preserveMeasuredStyle(clone.style, 'font-size', getComputedStyle(element).fontSize);
      const originals = [element.querySelector('img'), ...element.querySelectorAll('.overlay')];
      const copies = [clone.querySelector('img'), ...clone.querySelectorAll('.overlay')];
      originals.forEach((original, index) => {
        const rectangle = original.getBoundingClientRect();
        const style = copies[index].style;
        preserveMeasuredStyle(style, 'width', `${rectangle.width}px`);
        preserveMeasuredStyle(style, 'height', `${rectangle.height}px`);
        preserveMeasuredStyle(
          style,
          'font-size',
          original.style.getPropertyValue('--readability-font-size') ||
            original.style.fontSize ||
            getComputedStyle(original).fontSize
        );
        if (original.classList.contains('overlay')) {
          preserveMeasuredStyle(style, 'left', `${rectangle.left - origin.left}px`);
          preserveMeasuredStyle(style, 'top', `${rectangle.top - origin.top}px`);
        }
      });
      const svg = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(new XMLSerializer().serializeToString(svgDocument))}`;
      const image = new Image();
      image.src = svg;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = Math.ceil(width * PDF_PIXEL_RATIO);
      canvas.height = Math.ceil(height * PDF_PIXEL_RATIO);
      const context = canvas.getContext('2d');
      context.fillStyle = '#fff';
      context.fillRect(0, 0, canvas.width, canvas.height);
      // Preserve fractional CSS geometry instead of stretching to integer canvas dimensions.
      context.drawImage(image, 0, 0, width * PDF_PIXEL_RATIO, height * PDF_PIXEL_RATIO);
      const format = [width * 0.75, height * 0.75];
      const orientation = width > height ? 'landscape' : 'portrait';
      if (!pdf) {
        pdf = new globalThis.jspdf.jsPDF({ unit: 'pt', format, orientation, compress: true });
        pdf.setProperties({ title: snapshot.meta.manual_name || manualName });
      } else pdf.addPage(format, orientation);
      pdf.addImage(
        canvas,
        'PNG',
        0,
        0,
        canvas.width * 0.375,
        canvas.height * 0.375,
        undefined,
        'FAST'
      );
      canvas.width = canvas.height = 0;
      // Yield between pages so progress paints and large manuals stay responsive.
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    const filename = `${manualName.replace(/[\\/:*?"<>|]/g, '_')}.pdf`;
    const blob = pdf.output('blob');
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    return { pageCount: snapshot.pages.length, bytes: blob.size };
  } finally {
    capture.cleanup();
  }
}
