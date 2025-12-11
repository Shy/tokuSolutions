// Configuration and constants
export const BBOX_DEFAULTS = {
    X_RATIO: 0.4,        // 40% from left edge
    Y_RATIO: 0.45,       // 45% from top edge
    WIDTH_RATIO: 0.2,    // 20% of page width
    HEIGHT_RATIO: 0.1    // 10% of page height
};

export const BBOX_CONSTRAINTS = {
    MIN_WIDTH: 10,       // Minimum bbox width in pixels
    MIN_HEIGHT: 10,      // Minimum bbox height in pixels
    MIN_X: 0,           // Minimum x coordinate
    MIN_Y: 0            // Minimum y coordinate
};

export const UI_TIMINGS = {
    SCROLL_DETECT_THRESHOLD: 100,  // pixels from top to detect current page
    SCROLL_THROTTLE: 150,           // ms between scroll event processing
    FORK_WAIT_TIME: 2000            // ms to wait for GitHub fork to initialize
};

export const GITHUB_CONFIG = {
    CLIENT_ID: 'Ov23livUs1jiLOnUsIUN',
    REPO: 'shy/tokuSolutions'
};
