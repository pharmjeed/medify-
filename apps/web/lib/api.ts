export type ApiEnvelope<T> = { data: T; meta: Record<string, unknown> };

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = typeof window !== "undefined" ? localStorage.getItem("medify_token") : null;
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`/api/v1${path}`, { ...options, headers, cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload?.error?.message_ar || "تعذر إكمال الطلب");
  return (payload as ApiEnvelope<T>).data;
}

