import axios from 'axios';
import toast from 'react-hot-toast';
import Cookies from 'js-cookie';

const getInitialBaseUrl = (): string => {
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL;
  }
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
    if (window.location.protocol === 'https:') {
      return `${window.location.origin}/api/v1`;
    }
    return `${window.location.protocol}//${window.location.hostname}:8000/api/v1`;
  }
  return 'http://localhost:8000/api/v1';
};

const baseURL = getInitialBaseUrl();

export const apiClient = axios.create({
  baseURL,
  withCredentials: true,
});

apiClient.interceptors.request.use((config) => {
  if (typeof window !== 'undefined') {
    const host = window.location.hostname;
    const isLocal = host === 'localhost' || host === '127.0.0.1';

    if (!isLocal) {
      if (process.env.NEXT_PUBLIC_API_URL && !process.env.NEXT_PUBLIC_API_URL.includes('localhost')) {
        config.baseURL = process.env.NEXT_PUBLIC_API_URL;
      } else if (window.location.protocol === 'https:' && (!config.baseURL || config.baseURL.includes(':8000'))) {
        // Strip out :8000 over HTTPS since Nginx proxies /api/ via port 443
        config.baseURL = `${window.location.origin}/api/v1`;
      } else if (!config.baseURL || config.baseURL.includes('localhost')) {
        config.baseURL = `${window.location.protocol}//${host}:8000/api/v1`;
      }
    }
  }

  const activeOrgId = Cookies.get('active_org_id');
  if (activeOrgId && config.headers) {
    config.headers['X-Organization-Id'] = activeOrgId;
  }
  return config;
});

let isRefreshing = false;
let failedQueue: Array<{
  resolve: (value?: unknown) => void;
  reject: (error: any) => void;
}> = [];

const processQueue = (error: any) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve();
    }
  });

  failedQueue = [];
};

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // If error is 401 and we haven't retried yet
    if (error.response?.status === 401 && !originalRequest._retry) {
      // If the refresh endpoint itself fails or login endpoint, don't loop
      if (originalRequest.url?.includes('/auth/refresh') || originalRequest.url?.includes('/auth/login')) {
        return Promise.reject(error);
      }

      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        })
          .then(() => {
            return apiClient(originalRequest);
          })
          .catch((err) => {
            return Promise.reject(err);
          });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        // Attempt to refresh the token via HTTP-only cookie
        await apiClient.post('/auth/refresh');

        isRefreshing = false;
        processQueue(null);

        // Retry original request
        return apiClient(originalRequest);
      } catch (refreshError) {
        isRefreshing = false;
        processQueue(refreshError);

        // Refresh failed, usually means user needs to log in again
        toast.error('Session expired. Please log in again.', { id: 'session-expired' });

        if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
          window.location.href = '/login';
        }

        return Promise.reject(refreshError);
      }
    }

    // Show generic error toasts for non-401s if they are 500s or network errors
    if (!error.response) {
      toast.error('Network error. Please check your connection.', { id: 'network-error' });
    } else if (error.response.status >= 500) {
      toast.error('A server error occurred. Please try again later.', { id: 'server-error' });
    }

    return Promise.reject(error);
  }
);

export default apiClient;
