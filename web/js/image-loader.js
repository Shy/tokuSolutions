// Lazy image loading with Intersection Observer
export const ImageLoader = {
    observer: null,

    init() {
        if (!('IntersectionObserver' in window)) {
            // Fallback for older browsers - load all images immediately
            return;
        }

        this.observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    const src = img.dataset.src;
                    if (src) {
                        img.src = src;
                        img.removeAttribute('data-src');
                        this.observer.unobserve(img);
                    }
                }
            });
        }, {
            rootMargin: '50px' // Start loading 50px before entering viewport
        });
    },

    observe(img) {
        if (!this.observer) {
            // Fallback - load immediately
            if (img.dataset.src) {
                img.src = img.dataset.src;
                img.removeAttribute('data-src');
            }
            return;
        }
        this.observer.observe(img);
    },

    disconnect() {
        if (this.observer) {
            this.observer.disconnect();
        }
    }
};
