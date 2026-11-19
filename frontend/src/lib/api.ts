import axios, { AxiosError } from "axios";

const TOKEN_KEY = "scx_token";

export const tokenStore = {
  get: (): string | null => {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set: (t: string) => {
    try {
      localStorage.setItem(TOKEN_KEY, t);
    } catch {
      /* storage unavailable */
    }
  },
  clear: () => {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage unavailable */
    }
  },
};

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL || "", timeout: 120_000 });

api.interceptors.request.use((cfg) => {
  const t = tokenStore.get();
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});

api.interceptors.response.use(
  (r) => r,
  (err: AxiosError) => {
    if (err.response?.status === 401 && !err.config?.url?.includes("/api/auth/")) {
      tokenStore.clear();
      window.dispatchEvent(new Event("scx:unauthorized"));
    }
    return Promise.reject(err);
  },
);

export interface FieldError {
  field?: string;
  column?: string;
  row?: number | null;
  message: string;
}

/** Human-readable message from any API error. */
export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as { detail?: unknown; errors?: FieldError[] } | undefined;
    if (data?.detail && typeof data.detail === "string") {
      const first = data.errors?.[0];
      if (data.detail === "Invalid request" && first) return `${first.field ?? ""}: ${first.message}`;
      return data.detail;
    }
    if (err.code === "ECONNABORTED") return "The request timed out.";
    if (!err.response) return "Cannot reach the SupplyChainX API. Is the backend running?";
    return `Request failed (${err.response.status})`;
  }
  return err instanceof Error ? err.message : "Unexpected error";
}

export async function download(path: string, params: Record<string, string | undefined>) {
  const res = await api.get(path, { params, responseType: "blob" });
  const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
  const name = /filename="?([^"]+)"?/.exec(cd)?.[1] ?? "download";
  const url = URL.createObjectURL(res.data as Blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
