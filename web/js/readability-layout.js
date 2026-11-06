// Layout uses normalized page geometry; no scan DPI or fixed font minimum.
export function usesReadabilityLayout(page) {
  return (
    page.layout_provenance?.readability?.version === 1 ||
    page.layout_provenance?.structure?.version === 1
  );
}

const median = (values) => {
  const sorted = values.filter((value) => value > 0).sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] || 0;
};
export function intersects(a, b, gap = 0) {
  return (
    a[0] < b[0] + b[2] + gap &&
    a[0] + a[2] + gap > b[0] &&
    a[1] < b[1] + b[3] + gap &&
    a[1] + a[3] + gap > b[1]
  );
}

export function addedInkOutside(candidate, allowance, ink) {
  const left = Math.max(candidate[0], allowance[0]);
  const top = Math.max(candidate[1], allowance[1]);
  const right = Math.min(candidate[0] + candidate[2], allowance[0] + allowance[2]);
  const bottom = Math.min(candidate[1] + candidate[3], allowance[1] + allowance[3]);
  return ink(candidate) - ink([left, top, Math.max(0, right - left), Math.max(0, bottom - top)]);
}

export function structureAllowsBox(page, original, candidate) {
  const contains = (outer, inner) =>
    inner[0] >= outer[0] - 0.0015 &&
    inner[1] >= outer[1] - 0.0015 &&
    inner[0] + inner[2] <= outer[0] + outer[2] + 0.0015 &&
    inner[1] + inner[3] <= outer[1] + outer[3] + 0.0015;
  const area = (a, b) =>
    Math.max(0, Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0])) *
    Math.max(0, Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]));
  return (page.layout_provenance?.structure?.variants || []).every((variant) =>
    variant.regions.every((region) => {
      if (region.kind === 'cell' && contains(region.bbox, original))
        return contains(region.bbox, candidate);
      return (
        region.kind !== 'picture' ||
        region.bbox[2] * region.bbox[3] >= 0.65 ||
        area(candidate, region.bbox) <= area(original, region.bbox) + 1e-9
      );
    })
  );
}

export function displayBlocksForPage(page) {
  return page.blocks.flatMap((block, blockIndex) => {
    const projection = page.layout_provenance?.row_layout?.[blockIndex];
    if (projection?.translation === block.translation && projection.rows?.length > 0) {
      return projection.rows.map((row, rowIndex) => ({
        ...row,
        blockIndex,
        rowIndex,
        layout: 'panel',
        isTableRow: true
      }));
    }
    return [{ ...block, blockIndex }];
  });
}

export function rolesForPage(page) {
  const panels = new Map(
    (page.layout_provenance?.plan?.panels || []).map((panel) => [panel.id, panel.kind])
  );
  const mapping = new Map(
    (page.layout_provenance?.mapping || []).map((row) => [row.display_index, row])
  );
  return displayBlocksForPage(page).map((block) => {
    const index = block.blockIndex;
    const kind = panels.get(mapping.get(index)?.panel_id);
    const role = block.isTableRow
      ? 'table'
      : kind === 'heading' && block.translation.length < 60
        ? 'heading'
        : kind === 'prose' || block.translation.length > 70
          ? 'body'
          : 'label';
    return { ...block, role };
  });
}

export function layoutReadabilityPage({ blocks, width, height, measure, isSafe, scale = 1 }) {
  const references = new Map();
  for (const block of blocks) {
    const sourceLines = Math.max(1, block.text.split('\n').length);
    const reference = (block.bbox[3] * height) / sourceLines / 1.15;
    const role = block.role || 'body';
    if (!references.has(role)) references.set(role, []);
    references.get(role).push(reference);
  }
  const placed = [];
  return blocks.map((block, index) => {
    const original = [...block.bbox];
    const role = block.role || 'body';
    // The source's line scale anchors each role; robust medians resist tiny
    // leftover fragments and unusually tall multiline OCR regions.
    const target =
      Math.min(width * 0.024, Math.max(width * 0.007, median(references.get(role)))) * scale;
    const padding = Math.min(width * 0.0012, original[3] * height * 0.08);
    const content = (bbox) => ({
      width: Math.max(0.01, bbox[2] * width - padding * 2),
      height: Math.max(0.01, bbox[3] * height - padding * 2)
    });
    const fits = (bbox, font) => {
      const box = content(bbox),
        actual = measure(block.translation, font, box.width);
      return (
        actual.width <= box.width + 0.02 &&
        actual.height <= box.height + 0.02 &&
        actual.wordWidth <= box.width + 0.02
      );
    };
    const collides = (bbox) =>
      blocks.some(
        (other, otherIndex) => otherIndex !== index && intersects(bbox, other.bbox, 0.0005)
      ) || placed.some((other) => intersects(bbox, other, 0.0005));
    let bbox = [...original];
    let safe = !collides(bbox);
    for (let attempt = 0; safe && !fits(bbox, target) && attempt < 64; attempt++) {
      const [x, y, w, h] = bbox;
      const candidates = [
        [x, y, w + 0.006, h],
        [x - 0.006, y, w + 0.006, h],
        [x, y, w, h + 0.003],
        [x, y - 0.003, w, h + 0.003]
      ].filter(
        (candidate) =>
          candidate[0] >= 0.005 &&
          candidate[1] >= 0.005 &&
          candidate[0] + candidate[2] <= 0.995 &&
          candidate[1] + candidate[3] <= 0.995 &&
          candidate[2] <= original[2] + 0.18 &&
          candidate[3] <= original[3] + 0.08 &&
          !collides(candidate) &&
          isSafe(original, candidate, index)
      );
      if (!candidates.length) break;
      // Prefer width until the longest word fits, then minimize line count and
      // overflowing height. Never cross a protected pixel or source region.
      candidates.sort((a, b) => b[2] - a[2] || a[3] - b[3]);
      bbox = candidates[0];
    }
    // Consistency is a preference; every instruction stays on the scan.
    if (!safe) {
      for (const other of blocks.filter((_, otherIndex) => otherIndex !== index)) {
        if (!intersects(bbox, other.bbox)) continue;
        const [x, y, w, h] = bbox,
          [ox, oy, ow, oh] = other.bbox;
        const choices = [
          [x, y, Math.max(0, ox - x), h],
          [ox + ow, y, Math.max(0, x + w - ox - ow), h],
          [x, y, w, Math.max(0, oy - y)],
          [x, oy + oh, w, Math.max(0, y + h - oy - oh)]
        ].filter((candidate) => candidate[2] > 0 && candidate[3] > 0);
        choices.sort((a, b) => b[2] * b[3] - a[2] * a[3]);
        if (choices.length) bbox = choices[0];
      }
    }
    let low = target * 0.03,
      high = target;
    for (let attempt = 0; attempt < 20; attempt++) {
      const middle = (low + high) / 2;
      if (fits(bbox, middle)) low = middle;
      else high = middle;
    }
    const font = Math.max(0.25, Math.floor((fits(bbox, target) ? target : low) * 4) / 4);
    placed.push(bbox);
    return {
      bbox,
      padding,
      fontSize: font,
      placement: 'overlay',
      role,
      status: fits(bbox, font) ? 'fits' : 'overflow',
      constrained: font < target * 0.75
    };
  });
}

// A summed-area ink map makes candidate checks independent of OCR boxes. It
// protects unrecognized diagrams, callout borders and table rules as well.
export function imageWhitespaceSafety(image, page, projection = page.blocks) {
  const canvas = document.createElement('canvas');
  canvas.width = Math.min(900, image.naturalWidth);
  canvas.height = Math.ceil((canvas.width * image.naturalHeight) / image.naturalWidth);
  const context = canvas.getContext('2d', { willReadFrequently: true });
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  const stride = canvas.width + 1,
    integral = new Uint32Array(stride * (canvas.height + 1));
  for (let y = 0; y < canvas.height; y++) {
    let sum = 0;
    for (let x = 0; x < canvas.width; x++) {
      const offset = (y * canvas.width + x) * 4;
      sum += Math.min(pixels[offset], pixels[offset + 1], pixels[offset + 2]) < 235 ? 1 : 0;
      integral[(y + 1) * stride + x + 1] = integral[y * stride + x + 1] + sum;
    }
  }
  const ink = (box) => {
    const left = Math.max(0, Math.floor(box[0] * canvas.width)),
      top = Math.max(0, Math.floor(box[1] * canvas.height));
    const right = Math.min(canvas.width, Math.ceil((box[0] + box[2]) * canvas.width)),
      bottom = Math.min(canvas.height, Math.ceil((box[1] + box[3]) * canvas.height));
    return (
      integral[bottom * stride + right] -
      integral[top * stride + right] -
      integral[bottom * stride + left] +
      integral[top * stride + left]
    );
  };
  const rows = page.layout_provenance?.mapping || [];
  const source = page.source_blocks || page.blocks;
  const ownership = new Map(
    rows
      .filter((row) => row.display_index >= 0)
      .map((row) => [row.display_index, new Set([...row.source_ids, ...row.furigana_ids])])
  );
  return (original, candidate, index) => {
    if (!structureAllowsBox(page, original, candidate)) return false;
    if (projection[index].isTableRow) return false;
    if (
      source.some(
        (block, identity) =>
          block.bbox &&
          !ownership.get(projection[index].blockIndex ?? index)?.has(identity) &&
          intersects(candidate, block.bbox) &&
          !intersects(original, block.bbox)
      )
    )
      return false;
    // A two-pixel allowance handles fractional scan-boundary rounding only;
    // accumulation still rejects continuous borders or artwork across steps.
    const allowance = [
      original[0] - 0.0015,
      original[1] - 0.0015,
      original[2] + 0.003,
      original[3] + 0.003
    ];
    return addedInkOutside(candidate, allowance, ink) <= 2;
  };
}
