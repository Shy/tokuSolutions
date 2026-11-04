const DEFAULT_TOLERANCE = 0.1;
const DEFAULT_MAX_ITERATIONS = 24;
// CSS layout is quantized; this is less than one fiftieth of a CSS pixel.
const LAYOUT_EPSILON = 1 / 64;

function isWithinBox(measurement, box) {
  return (
    measurement.width <= box.width + LAYOUT_EPSILON &&
    measurement.height <= box.height + LAYOUT_EPSILON
  );
}

export function fitText({
  text,
  box,
  minFontSize,
  maxFontSize,
  measure,
  tolerance = DEFAULT_TOLERANCE,
  maxIterations = DEFAULT_MAX_ITERATIONS,
  generation = 0,
  isCurrentGeneration = () => true
}) {
  if (text.trim() === '') {
    return { status: 'missing', fontSize: null, iterations: 0 };
  }

  if (!isCurrentGeneration(generation)) {
    return { status: 'stale', fontSize: null, iterations: 0 };
  }

  if (!isWithinBox(measure(minFontSize), box)) {
    return { status: 'overflow', fontSize: minFontSize, iterations: 0 };
  }

  if (!isCurrentGeneration(generation)) {
    return { status: 'stale', fontSize: null, iterations: 0 };
  }

  if (isWithinBox(measure(maxFontSize), box)) {
    return { status: 'fits', fontSize: maxFontSize, iterations: 0 };
  }

  let lower = minFontSize;
  let upper = maxFontSize;
  let iterations = 0;

  while (upper - lower > tolerance && iterations < maxIterations) {
    if (!isCurrentGeneration(generation)) {
      return { status: 'stale', fontSize: null, iterations };
    }

    const candidate = (lower + upper) / 2;
    if (isWithinBox(measure(candidate), box)) {
      lower = candidate;
    } else {
      upper = candidate;
    }
    iterations += 1;
  }

  return { status: 'fits', fontSize: lower, iterations };
}
