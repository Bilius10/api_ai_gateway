import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export interface Provider {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  priority: number;
  base_url: string | null;
  model: string;
  api_key_env: string | null;
  timeout_seconds: number;
  supports_stream: boolean;
}

export interface ProviderWrite extends Provider {
  api_key?: string | null;
}

export interface RequestRecord {
  id: string;
  requested_provider: string | null;
  selected_provider: string | null;
  model: string | null;
  status: string;
  latency_ms: number;
  attempt_count: number;
  created_at: string;
}

export interface Attempt {
  provider_id: string;
  provider_name: string;
  success: boolean;
  latency_ms: number;
  error_kind: string | null;
  error_message: string | null;
}

export interface RequestDetail {
  request: RequestRecord;
  attempts: Attempt[];
}

export interface Metrics {
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  fallback_requests: number;
  average_latency_ms: number;
  providers: Record<string, Record<string, number>>;
}

export interface GenerateResponse {
  request_id: string;
  provider: string;
  model: string;
  response: string;
  attempts: Array<{provider_id: string; success: boolean; error_kind?: string}>;
}

export interface StreamDone {
  request_id: string;
  provider: string;
  model: string;
  attempts: Attempt[];
}

@Injectable({ providedIn: 'root' })
export class GatewayApi {
  private readonly http = inject(HttpClient);
  readonly base = '/api';

  health(): Observable<{status: string; warning: string}> { return this.http.get<{status: string; warning: string}>(`${this.base}/health`); }
  metrics(): Observable<Metrics> { return this.http.get<Metrics>(`${this.base}/metrics`); }
  providers(): Observable<Provider[]> { return this.http.get<Provider[]>(`${this.base}/providers`); }
  createProvider(provider: ProviderWrite): Observable<Provider> { return this.http.post<Provider>(`${this.base}/providers`, provider); }
  updateProvider(provider: ProviderWrite): Observable<Provider> { return this.http.put<Provider>(`${this.base}/providers/${provider.id}`, provider); }
  deleteProvider(id: string): Observable<void> { return this.http.delete<void>(`${this.base}/providers/${id}`); }
  providerHealth(id: string): Observable<{healthy: boolean; detail: string}> { return this.http.get<{healthy: boolean; detail: string}>(`${this.base}/providers/${id}/health`); }
  providerModels(id: string): Observable<{provider: string; models: string[]}> { return this.http.get<{provider: string; models: string[]}>(`${this.base}/providers/${id}/models`); }
  requests(): Observable<RequestRecord[]> { return this.http.get<RequestRecord[]>(`${this.base}/requests`); }
  requestDetail(id: string): Observable<RequestDetail> { return this.http.get<RequestDetail>(`${this.base}/requests/${id}`); }
  routing(): Observable<{fallback_enabled: boolean; max_attempts: number}> { return this.http.get<{fallback_enabled: boolean; max_attempts: number}>(`${this.base}/routing`); }
  updateRouting(value: {fallback_enabled: boolean; max_attempts: number}): Observable<typeof value> { return this.http.put<typeof value>(`${this.base}/routing`, value); }
  settings(): Observable<{retention_days: number; max_prompt_chars: number}> { return this.http.get<{retention_days: number; max_prompt_chars: number}>(`${this.base}/settings`); }
  updateSettings(value: {retention_days: number; max_prompt_chars: number}): Observable<typeof value> { return this.http.put<typeof value>(`${this.base}/settings`, value); }
  generate(prompt: string, provider: string | null): Observable<GenerateResponse> { return this.http.post<GenerateResponse>(`${this.base}/generate`, {prompt, provider}); }
}
