import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";
import toast from "react-hot-toast";

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

let isRefreshing = false;
let pendingRequests: Array<(token: string) => void> = [];

export const api = axios.create({
  baseURL: `${API_BASE_URL}/api/v1`,
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
  },
});

// Request interceptor for auth
api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem("access_token");
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    if (error.response?.status === 401) {
      if (error.config?.url?.includes('/auth/refresh')) {
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
        window.location.href = "/login";
        return Promise.reject(error);
      }
      const refreshToken = localStorage.getItem("refresh_token");
      if (refreshToken) {
        if (isRefreshing) {
          return new Promise((resolve) => {
            pendingRequests.push((token: string) => {
              error.config!.headers!.Authorization = `Bearer ${token}`;
              resolve(axios(error.config!));
            });
          });
        }
        isRefreshing = true;
        try {
          const response = await axios.post(`${API_BASE_URL}/api/v1/auth/refresh`, {
            refresh_token: refreshToken,
          });
          const { access_token, refresh_token: newRefresh } = response.data;
          localStorage.setItem("access_token", access_token);
          localStorage.setItem("refresh_token", newRefresh);
          pendingRequests.forEach((cb) => cb(access_token));
          pendingRequests = [];
          error.config!.headers!.Authorization = `Bearer ${access_token}`;
          return axios(error.config!);
        } catch {
          pendingRequests = [];
          localStorage.removeItem("access_token");
          localStorage.removeItem("refresh_token");
          window.location.href = "/login";
        } finally {
          isRefreshing = false;
        }
      } else {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

// Types
export interface Document {
  id: string;
  tenant_id: string;
  filename: string;
  original_filename: string;
  content_type: string;
  file_size: number;
  page_count?: number;
  status: DocumentStatus;
  document_type?: DocumentType;
  decision?: Decision;
  error_message?: string;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at?: string;
}

export type DocumentStatus =
  | "pending"
  | "preprocessing"
  | "preprocess_failed"
  | "ocr"
  | "ocr_failed"
  | "classified"
  | "structuring"
  | "structuring_failed"
  | "reconciliation"
  | "reconciliation_failed"
  | "validating"
  | "validation_failed"
  | "completed"
  | "review"
  | "manual_review";

export type DocumentType =
  | "invoice"
  | "proforma"
  | "delivery_note"
  | "contract"
  | "bank_statement"
  | "official_document";

export type Decision = "auto" | "review" | "manual";

export interface DocumentListResponse {
  total: number;
  skip: number;
  limit: number;
  items: Document[];
}

export interface DocumentResult {
  document_id: string;
  status: DocumentStatus;
  document_type?: DocumentType;
  decision?: Decision;
  ocr_result?: {
    full_text: string;
    text_blocks: TextBlock[];
    page_count: number;
  };
  structured_data?: {
    extracted_data: Record<string, unknown>;
    field_confidences: Record<string, number>;
    document_type: DocumentType;
    /** field_name → resolved text block with bbox (set by structuring worker) */
    bbox_evidence?: Record<string, TextBlock>;
  };
  reconciliation?: {
    status: "pass" | "warn" | "fail";
    subtotal_match?: boolean;
    vat_match?: boolean;
    total_match?: boolean;
    discrepancy_details?: Record<string, unknown>;
  };
}

export interface TextBlock {
  text: string;
  bbox?: BoundingBox;
  confidence?: number;
  page?: number;
  block_id?: string;
  block_type?: string;
}

export interface BoundingBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface Field {
  key: string;
  value: unknown;
  confidence: number;
  bbox?: BoundingBox;
}

// API functions
export const documentsApi = {
  list: async (
    skip = 0,
    limit = 20,
    status?: DocumentStatus,
    documentType?: DocumentType
  ): Promise<DocumentListResponse> => {
    const params = new URLSearchParams({ skip: String(skip), limit: String(limit) });
    if (status) params.append("status", status);
    if (documentType) params.append("document_type", documentType);
    
    const response = await api.get(`/documents?${params}`);
    return response.data;
  },

  get: async (documentId: string): Promise<Document> => {
    const response = await api.get(`/documents/${documentId}`);
    return response.data;
  },

  getResult: async (documentId: string): Promise<DocumentResult> => {
    const response = await api.get(`/documents/${documentId}/result`);
    return response.data;
  },

  upload: async (file: File): Promise<Document> => {
    const formData = new FormData();
    formData.append("file", file);
    
    const response = await api.post("/documents/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return response.data;
  },

  update: async (
    documentId: string,
    data: { decision?: Decision; document_type?: DocumentType; metadata?: Record<string, unknown> }
  ): Promise<Document> => {
    const response = await api.patch(`/documents/${documentId}`, data);
    return response.data;
  },

  getPageImageUrl: async (
    documentId: string,
    page = 1
  ): Promise<{ url: string; page: number; width?: number; height?: number }> => {
    const response = await api.get(`/documents/${documentId}/pages/${page}/image`);
    return response.data;
  },

  updateFields: async (
    documentId: string,
    fields: Record<string, unknown>
  ): Promise<{ document_id: string; updated_fields: string[] }> => {
    const response = await api.patch(`/documents/${documentId}/fields`, { fields });
    return response.data;
  },

  delete: async (documentId: string): Promise<void> => {
    await api.delete(`/documents/${documentId}`);
  },
};

export const authApi = {
  login: async (email: string, password: string) => {
    const response = await api.post("/auth/login", { email, password });
    return response.data;
  },

  register: async (name: string, email: string, password: string) => {
    const response = await api.post("/auth/register", { name, email, password });
    return response.data;
  },

  refresh: async (refreshToken: string) => {
    const response = await api.post("/auth/refresh", { refresh_token: refreshToken });
    return response.data;
  },
};

export const webhooksApi = {
  list: async () => {
    const response = await api.get("/webhooks");
    return response.data;
  },

  create: async (data: {
    name: string;
    url: string;
    events: string[];
  }) => {
    const response = await api.post("/webhooks", data);
    return response.data;
  },

  delete: async (webhookId: string) => {
    await api.delete(`/webhooks/${webhookId}`);
  },
};
