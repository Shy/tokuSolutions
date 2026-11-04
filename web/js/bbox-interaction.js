const DEFAULT_MIN_SIZE = Object.freeze({ width: 0.02, height: 0.02 });

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function roundBbox(bbox) {
  return bbox.map((value) => Number(value.toFixed(6)));
}

export function normalizedPointerDelta(delta, pageSize) {
  return {
    x: delta.x / pageSize.width,
    y: delta.y / pageSize.height
  };
}

export function beginBboxGesture({
  kind,
  handle = null,
  bbox,
  pointer,
  minSize = DEFAULT_MIN_SIZE
}) {
  return {
    kind,
    handle,
    before: roundBbox(bbox),
    current: roundBbox(bbox),
    startPointer: { ...pointer },
    minSize: { ...minSize }
  };
}

export function updateBboxGesture(gesture, pointer) {
  const dx = pointer.x - gesture.startPointer.x;
  const dy = pointer.y - gesture.startPointer.y;
  const [x, y, width, height] = gesture.before;

  if (gesture.kind === 'drag') {
    return {
      ...gesture,
      current: roundBbox([clamp(x + dx, 0, 1 - width), clamp(y + dy, 0, 1 - height), width, height])
    };
  }

  let left = x;
  let top = y;
  let right = x + width;
  let bottom = y + height;
  const handle = gesture.handle ?? '';

  if (handle.includes('w')) {
    left = clamp(x + dx, 0, right - gesture.minSize.width);
  }
  if (handle.includes('e')) {
    right = clamp(right + dx, left + gesture.minSize.width, 1);
  }
  if (handle.includes('n')) {
    top = clamp(y + dy, 0, bottom - gesture.minSize.height);
  }
  if (handle.includes('s')) {
    bottom = clamp(bottom + dy, top + gesture.minSize.height, 1);
  }

  return {
    ...gesture,
    current: roundBbox([left, top, right - left, bottom - top])
  };
}

export function commitBboxGesture(gesture) {
  return {
    before: [...gesture.before],
    after: [...gesture.current]
  };
}

export function cancelBboxGesture(gesture) {
  return [...gesture.before];
}
