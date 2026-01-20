export const API_URL = import.meta.env.VITE_API_URL || '';

export const API_ENDPOINTS = {
  AUTH: {
    ME: `${API_URL}/api/auth/me`,
    START: `${API_URL}/api/auth/strava/start`,
  },
  QUERY: `${API_URL}/api/query`,
} as const;

