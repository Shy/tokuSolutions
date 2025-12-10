// Centralized error handling
export const ErrorHandler = {
    // Log error and show user-friendly message
    handle(error, context = '', userMessage = null) {
        console.error(`[${context}]`, error);

        if (userMessage) {
            alert(userMessage);
        }

        return false;
    },

    // Network errors
    network(error, endpoint) {
        return this.handle(
            error,
            'Network Error',
            `Failed to load data from ${endpoint}.\n\n` +
            `Please check your internet connection and try again.\n\n` +
            `Error: ${error.message}`
        );
    },

    // GitHub API errors
    github(error, operation) {
        let message = `GitHub operation failed: ${operation}\n\n`;

        if (error.status === 401) {
            message += 'Authentication failed. Your token may have expired.\n\n';
            localStorage.removeItem('github_token');
        } else if (error.status === 403) {
            message += 'Permission denied. Check your repository permissions.\n\n';
        } else if (error.status === 404) {
            message += 'Repository or file not found.\n\n';
        } else {
            message += `${error.message}\n\n`;
        }

        message += 'You can download the JSON file instead.';

        return this.handle(error, 'GitHub Error', message);
    },

    // Validation errors
    validation(message, details = '') {
        return this.handle(
            new Error(details),
            'Validation Error',
            message
        );
    },

    // Generic user-facing errors
    user(message, error = null) {
        return this.handle(
            error || new Error(message),
            'User Error',
            message
        );
    }
};
