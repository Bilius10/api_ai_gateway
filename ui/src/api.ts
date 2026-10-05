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
  attempts: Attempt[];
}

export interface StreamDone {
  request_id: string;
  provider: string;
  model: string;
  attempts: Attempt[];
}

export interface StreamEvent {
  type: string;
  data: Record<string, unknown>;
}

interface DesktopAPIResponse {
  status: number;
  headers: Record<string, string>;
  body: string;
}

interface DesktopBridge {
  APIRequest(method: string, path: string, body: string): Promise<DesktopAPIResponse>;
  BackendStatus?: () => Promise<{ state: string; message: string }>;
  RestartBackend?: () => Promise<void>;
}

declare global {
  interface Window {
    go?: { main?: { App?: DesktopBridge } };
  }
}

export const desktopTransport: typeof fetch = async (input, init = {}) => {
  const bridge = window.go?.main?.App;
  if (!bridge) return fetch(input, init);
  if (init.signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError');

  const raw = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
  const parsed = new URL(raw, 'http://wails.localhost');
  const path = `${parsed.pathname}${parsed.search}`;
  const body = typeof init.body === 'string' ? init.body : '';
  const result = await bridge.APIRequest(String(init.method ?? 'GET'), path, body);
  return new Response(result.body, {
    status: result.status,
    headers: result.headers,
  });
};

export class SSEParser {
  private buffer = '';

  feed(chunk: string): StreamEvent[] {
    this.buffer = (this.buffer + chunk).replaceAll('\r\n', '\n').replaceAll('\r', '\n');
    const events: StreamEvent[] = [];
    while (true) {
      const boundary = this.buffer.indexOf('\n\n');
      if (boundary < 0) break;
      const raw = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const event = parseEvent(raw);
      if (event) events.push(event);
    }
    return events;
  }

  finish(): StreamEvent[] {
    const raw = this.buffer.trim();
    this.buffer = '';
    const event = parseEvent(raw);
    return event ? [event] : [];
  }
}

function parseEvent(raw: string): StreamEvent | null {
  if (!raw.trim()) return null;
  let type = 'message';
  const data: string[] = [];
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) type = line.slice(6).trim();
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  const parsed: unknown = JSON.parse(data.join('\n'));
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Gateway stream returned invalid data.');
  }
  return { type, data: parsed as Record<string, unknown> };
}

export function providerPayload(provider: Provider, apiKey?: string | null): ProviderWrite {
  const payload: ProviderWrite = { ...provider };
  if (apiKey !== undefined) payload.api_key = apiKey;
  return payload;
}

export class GatewayApi {
  constructor(
    readonly base = '/api',
    private readonly transport: typeof fetch = desktopTransport,
  ) {}

  health(): Promise<{ status: string; warning: string }> { return this.request('/health'); }
  metrics(): Promise<Metrics> { return this.request('/metrics'); }
  providers(): Promise<Provider[]> { return this.request('/providers'); }
  createProvider(provider: ProviderWrite): Promise<Provider> { return this.request('/providers', { method: 'POST', body: JSON.stringify(provider) }); }
  updateProvider(provider: ProviderWrite): Promise<Provider> { return this.request(`/providers/${encodeURIComponent(provider.id)}`, { method: 'PUT', body: JSON.stringify(provider) }); }
  deleteProvider(id: string): Promise<void> { return this.request(`/providers/${encodeURIComponent(id)}`, { method: 'DELETE' }); }
  providerHealth(id: string): Promise<{ healthy: boolean; detail: string }> { return this.request(`/providers/${encodeURIComponent(id)}/health`); }
  providerModels(id: string): Promise<{ provider: string; models: string[] }> { return this.request(`/providers/${encodeURIComponent(id)}/models`); }
  requests(): Promise<RequestRecord[]> { return this.request('/requests'); }
  requestDetail(id: string): Promise<RequestDetail> { return this.request(`/requests/${encodeURIComponent(id)}`); }
  routing(): Promise<{ fallback_enabled: boolean; max_attempts: number }> { return this.request('/routing'); }
  updateRouting(value: { fallback_enabled: boolean; max_attempts: number }): Promise<typeof value> { return this.request('/routing', { method: 'PUT', body: JSON.stringify(value) }); }
  settings(): Promise<{ retention_days: number; max_prompt_chars: number }> { return this.request('/settings'); }
  updateSettings(value: { retention_days: number; max_prompt_chars: number }): Promise<typeof value> { return this.request('/settings', { method: 'PUT', body: JSON.stringify(value) }); }
  generate(prompt: string, provider: string | null, signal?: AbortSignal): Promise<GenerateResponse> {
    return this.request('/generate', { method: 'POST', body: JSON.stringify({ prompt, provider }), signal });
  }

  async streamGenerate(
    prompt: string,
    provider: string | null,
    onChunk: (text: string) => void,
    signal?: AbortSignal,
  ): Promise<StreamDone> {
    const response = await this.transport(`${this.base}/generate/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, provider, stream: true }),
      signal,
    });
    if (!response.ok || !response.body) throw new Error('Streaming request failed.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parser = new SSEParser();
    let completed: StreamDone | undefined;
    const handle = (events: StreamEvent[]): void => {
      for (const event of events) {
        if (event.type === 'chunk') onChunk(String(event.data['text'] ?? ''));
        if (event.type === 'error') throw new Error('Provider stream failed.');
        if (event.type === 'done') completed = event.data as unknown as StreamDone;
      }
    };
    while (true) {
      const part = await reader.read();
      if (part.done) {
        handle(parser.feed(decoder.decode()));
        break;
      }
      handle(parser.feed(decoder.decode(part.value, { stream: true })));
    }
    handle(parser.finish());
    if (!completed) throw new Error('Stream ended without completion metadata.');
    return completed;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.transport(`${this.base}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
    });
    if (!response.ok) throw new Error(`Gateway request failed (${response.status}).`);
    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  }
}
