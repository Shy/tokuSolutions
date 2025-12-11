import { describe, it, expect, beforeEach, vi } from 'vitest';
import { debounce, throttle, sanitizeText, validateBbox, clampBbox } from '../js/utils.js';

describe('debounce', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('should delay function execution', () => {
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced();
    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(99);
    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(fn).toHaveBeenCalledOnce();
  });

  it('should cancel previous calls', () => {
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced();
    debounced();
    debounced();

    vi.advanceTimersByTime(100);
    expect(fn).toHaveBeenCalledOnce();
  });

  it('should pass arguments correctly', () => {
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced('arg1', 'arg2');
    vi.advanceTimersByTime(100);

    expect(fn).toHaveBeenCalledWith('arg1', 'arg2');
  });
});

describe('throttle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('should execute immediately on first call', () => {
    const fn = vi.fn();
    const throttled = throttle(fn, 100);

    throttled();
    expect(fn).toHaveBeenCalledOnce();
  });

  it('should block subsequent calls within wait period', () => {
    const fn = vi.fn();
    const throttled = throttle(fn, 100);

    throttled();
    throttled();
    throttled();

    expect(fn).toHaveBeenCalledOnce();
  });

  it('should allow call after wait period', () => {
    const fn = vi.fn();
    const throttled = throttle(fn, 100);

    throttled();
    expect(fn).toHaveBeenCalledOnce();

    vi.advanceTimersByTime(100);
    throttled();
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('should pass arguments correctly', () => {
    const fn = vi.fn();
    const throttled = throttle(fn, 100);

    throttled('test1');
    expect(fn).toHaveBeenCalledWith('test1');

    vi.advanceTimersByTime(100);
    throttled('test2');
    expect(fn).toHaveBeenCalledWith('test2');
  });
});

describe('sanitizeText', () => {
  it('should escape HTML special characters', () => {
    expect(sanitizeText('<script>alert("xss")</script>')).toBe('&lt;script&gt;alert("xss")&lt;/script&gt;');
    expect(sanitizeText('Hello & goodbye')).toBe('Hello &amp; goodbye');
    expect(sanitizeText('"quoted"')).toBe('"quoted"');
  });

  it('should handle plain text', () => {
    expect(sanitizeText('Hello World')).toBe('Hello World');
  });

  it('should handle empty string', () => {
    expect(sanitizeText('')).toBe('');
  });

  it('should handle special characters', () => {
    expect(sanitizeText('A < B > C & D')).toBe('A &lt; B &gt; C &amp; D');
  });
});

describe('validateBbox', () => {
  it('should accept valid bbox array', () => {
    const bbox = [0.1, 0.2, 0.3, 0.4];
    expect(validateBbox(bbox)).toEqual(bbox);
  });

  it('should reject non-array', () => {
    expect(validateBbox('not array')).toBe(null);
    expect(validateBbox({})).toBe(null);
    expect(validateBbox(null)).toBe(null);
  });

  it('should reject wrong length array', () => {
    expect(validateBbox([0.1, 0.2])).toBe(null);
    expect(validateBbox([0.1, 0.2, 0.3, 0.4, 0.5])).toBe(null);
  });

  it('should reject NaN values', () => {
    expect(validateBbox([NaN, 0.2, 0.3, 0.4])).toBe(null);
    expect(validateBbox([0.1, 'bad', 0.3, 0.4])).toBe(null);
  });

  it('should convert string numbers to floats', () => {
    expect(validateBbox(['0.1', '0.2', '0.3', '0.4'])).toEqual([0.1, 0.2, 0.3, 0.4]);
  });
});

describe('clampBbox', () => {
  it('should keep valid bbox unchanged', () => {
    const bbox = [0.1, 0.2, 0.3, 0.4];
    expect(clampBbox(bbox)).toEqual([0.1, 0.2, 0.3, 0.4]);
  });

  it('should clamp negative x to 0', () => {
    expect(clampBbox([-0.1, 0.2, 0.3, 0.4])[0]).toBe(0);
  });

  it('should clamp x > 1 to 1', () => {
    expect(clampBbox([1.5, 0.2, 0.3, 0.4])[0]).toBe(1);
  });

  it('should clamp negative y to 0', () => {
    expect(clampBbox([0.1, -0.2, 0.3, 0.4])[1]).toBe(0);
  });

  it('should clamp y > 1 to 1', () => {
    expect(clampBbox([0.1, 1.5, 0.3, 0.4])[1]).toBe(1);
  });

  it('should enforce minimum width of 0.01', () => {
    expect(clampBbox([0.5, 0.5, 0, 0.1])[2]).toBe(0.01);
  });

  it('should enforce minimum height of 0.01', () => {
    expect(clampBbox([0.5, 0.5, 0.1, 0])[3]).toBe(0.01);
  });

  it('should prevent width extending beyond right edge', () => {
    const result = clampBbox([0.8, 0.5, 0.5, 0.1]);
    expect(result[2]).toBeLessThanOrEqual(0.2); // max width when x=0.8 is 0.2
  });

  it('should prevent height extending beyond bottom edge', () => {
    const result = clampBbox([0.5, 0.8, 0.1, 0.5]);
    expect(result[3]).toBeLessThanOrEqual(0.2); // max height when y=0.8 is 0.2
  });

  it('should handle edge case at x=1', () => {
    const result = clampBbox([1.0, 0.5, 0.3, 0.1]);
    expect(result[0]).toBe(1);
    expect(result[2]).toBe(0.01); // minimal width since x is at edge
  });
});
