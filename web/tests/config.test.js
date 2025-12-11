import { describe, it, expect } from 'vitest';
import { BBOX_DEFAULTS, BBOX_CONSTRAINTS, UI_TIMINGS, GITHUB_CONFIG } from '../js/config.js';

describe('BBOX_DEFAULTS', () => {
  it('should have valid normalized coordinates', () => {
    expect(BBOX_DEFAULTS.X_RATIO).toBeGreaterThanOrEqual(0);
    expect(BBOX_DEFAULTS.X_RATIO).toBeLessThanOrEqual(1);
    expect(BBOX_DEFAULTS.Y_RATIO).toBeGreaterThanOrEqual(0);
    expect(BBOX_DEFAULTS.Y_RATIO).toBeLessThanOrEqual(1);
    expect(BBOX_DEFAULTS.WIDTH_RATIO).toBeGreaterThan(0);
    expect(BBOX_DEFAULTS.WIDTH_RATIO).toBeLessThanOrEqual(1);
    expect(BBOX_DEFAULTS.HEIGHT_RATIO).toBeGreaterThan(0);
    expect(BBOX_DEFAULTS.HEIGHT_RATIO).toBeLessThanOrEqual(1);
  });

  it('should not exceed boundaries', () => {
    const totalX = BBOX_DEFAULTS.X_RATIO + BBOX_DEFAULTS.WIDTH_RATIO;
    const totalY = BBOX_DEFAULTS.Y_RATIO + BBOX_DEFAULTS.HEIGHT_RATIO;

    expect(totalX).toBeLessThanOrEqual(1);
    expect(totalY).toBeLessThanOrEqual(1);
  });
});

describe('BBOX_CONSTRAINTS', () => {
  it('should have positive minimum values', () => {
    expect(BBOX_CONSTRAINTS.MIN_WIDTH).toBeGreaterThan(0);
    expect(BBOX_CONSTRAINTS.MIN_HEIGHT).toBeGreaterThan(0);
  });

  it('should have valid coordinate minimums', () => {
    expect(BBOX_CONSTRAINTS.MIN_X).toBe(0);
    expect(BBOX_CONSTRAINTS.MIN_Y).toBe(0);
  });
});

describe('UI_TIMINGS', () => {
  it('should have positive timing values', () => {
    expect(UI_TIMINGS.EDIT_MODE_REAPPLY_DELAY).toBeGreaterThan(0);
    expect(UI_TIMINGS.SCROLL_DETECT_THRESHOLD).toBeGreaterThan(0);
    expect(UI_TIMINGS.SCROLL_THROTTLE).toBeGreaterThan(0);
    expect(UI_TIMINGS.FORK_WAIT_TIME).toBeGreaterThan(0);
  });

  it('should have reasonable delay values', () => {
    // Delays should be less than 10 seconds
    expect(UI_TIMINGS.EDIT_MODE_REAPPLY_DELAY).toBeLessThan(10000);
    expect(UI_TIMINGS.SCROLL_THROTTLE).toBeLessThan(10000);
  });
});

describe('GITHUB_CONFIG', () => {
  it('should have required properties', () => {
    expect(GITHUB_CONFIG.CLIENT_ID).toBeDefined();
    expect(GITHUB_CONFIG.REPO).toBeDefined();
  });

  it('should have valid client ID format', () => {
    expect(GITHUB_CONFIG.CLIENT_ID).toMatch(/^[A-Za-z0-9]+$/);
  });

  it('should have valid repo format', () => {
    expect(GITHUB_CONFIG.REPO).toMatch(/^[\w-]+\/[\w-]+$/);
  });
});
