export function classifyPrintOrientation({ width, height }) {
  return width > height ? 'landscape' : 'portrait';
}

export function createPrintPreparation({
  loadImage,
  loadFont,
  render,
  fit,
  print,
  afterPrint = (cleanup) =>
    globalThis.window?.addEventListener('afterprint', cleanup, { once: true }),
  onState = () => {}
}) {
  let state = 'idle';
  let activeRepresentation = null;

  function setState(next) {
    state = next;
    onState(next);
  }

  function cleanup() {
    activeRepresentation?.cleanup();
    activeRepresentation = null;
    setState('idle');
  }

  return {
    get state() {
      return state;
    },

    cleanup,

    async prepare(manual, { printAnyway = false } = {}) {
      if (activeRepresentation) cleanup();
      setState('loading-assets');

      let assets;
      try {
        assets = await Promise.all(
          manual.pages.map(async (page, index) => {
            const asset = await loadImage(page, index);
            if (!asset?.decoded) throw new Error(`Page ${index + 1} did not decode`);
            return asset;
          })
        );
      } catch (cause) {
        setState('blocked');
        return { status: 'blocked', error: 'image', cause };
      }

      try {
        await loadFont();
      } catch (cause) {
        setState('blocked');
        return { status: 'blocked', error: 'font', cause };
      }

      setState('rendering');
      activeRepresentation = render(manual, assets);
      const results = await fit(activeRepresentation, manual, assets);
      const warnings = results.filter(
        (result) => result.status === 'overflow' || result.status === 'missing'
      );

      if (warnings.length > 0 && !printAnyway) {
        setState('warning');
        return {
          status: 'warning',
          warnings,
          requiresPrintAnyway: true
        };
      }

      setState('printing');
      afterPrint(cleanup);
      print();
      return { status: 'printed', warnings, requiresPrintAnyway: false };
    }
  };
}
