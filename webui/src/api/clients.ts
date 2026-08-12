import { http } from "@/api/http";

export type ClientItem = {
  id: number;
  name: string;
  is_active: boolean;
  status: string;
  daily_quota: number | null;
  daily_usage: number;
  quota_reset_at: string | null;
  rate_limit_per_min: number;
  max_concurrent: number;
  max_retries: number;
  created_at: string;
  last_used_at: string | null;
};

export async function fetchClients() {
  const res = await http.get<{ items: ClientItem[] }>("/admin/clients");
  return res.data.items;
}

export type CreateClientRequest = {
  name: string;
  daily_quota?: number | null;
  rate_limit_per_min?: number;
  max_concurrent?: number;
  max_retries?: number;
  is_active?: boolean;
  /** Optional manual token; omit or empty to auto-generate. */
  token?: string | null;
};

export async function createClient(payload: CreateClientRequest) {
  const res = await http.post<{ client: ClientItem; token: string }>("/admin/clients", payload);
  return res.data;
}

export async function rotateClientToken(clientId: number) {
  const res = await http.post<{ client_id: number; token: string }>(`/admin/clients/${clientId}/rotate`);
  return res.data;
}

export async function revealClientToken(clientId: number) {
  const res = await http.get<{ client_id: number; name: string; token: string }>(
    `/admin/clients/${clientId}/token`,
  );
  return res.data;
}

export type UpdateClientRequest = {
  daily_quota?: number | null;
  rate_limit_per_min?: number | null;
  max_concurrent?: number | null;
  max_retries?: number | null;
  is_active?: boolean | null;
};

export async function updateClient(clientId: number, payload: UpdateClientRequest) {
  const res = await http.put<ClientItem>(`/admin/clients/${clientId}`, payload);
  return res.data;
}

export type BatchClientRequest = {
  client_ids: number[];
  action: "enable" | "disable" | "delete";
};

export type BatchClientResponse = {
  success_count: number;
  failed_count: number;
  failed_items: Array<{
    client_id: number;
    error: string;
  }>;
};

export async function batchUpdateClients(payload: BatchClientRequest) {
  const res = await http.patch<BatchClientResponse>("/admin/clients/batch", payload);
  return res.data;
}
