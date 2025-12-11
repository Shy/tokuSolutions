// Utility functions

// Debounce function for performance
export function debounce(func, wait) {
  let timeout;
  return function (...args) {
    clearTimeout(timeout);
    timeout = setTimeout(() => func.apply(this, args), wait);
  };
}

// Throttle function for scroll events
export function throttle(func, wait) {
  let waiting = false;
  return function (...args) {
    if (waiting) return;
    waiting = true;
    func.apply(this, args);
    setTimeout(() => {
      waiting = false;
    }, wait);
  };
}

// Sanitize text to prevent XSS
export function sanitizeText(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Validate bbox format
export function validateBbox(bbox) {
  if (!Array.isArray(bbox) || bbox.length !== 4) {
    console.error('Invalid bbox format:', bbox);
    return null;
  }

  let [x, y, w, h] = bbox.map((v) => parseFloat(v));

  if ([x, y, w, h].some(isNaN)) {
    console.error('Bbox contains NaN values:', bbox);
    return null;
  }

  return [x, y, w, h];
}

// Clamp bbox to valid 0-1 range
export function clampBbox(bbox) {
  let [x, y, w, h] = bbox;

  x = Math.max(0, Math.min(1, x));
  y = Math.max(0, Math.min(1, y));
  w = Math.max(0.01, Math.min(1 - x, w));
  h = Math.max(0.01, Math.min(1 - y, h));

  return [x, y, w, h];
}
