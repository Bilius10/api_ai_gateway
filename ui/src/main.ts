import './styles.css';

import {
  GatewayApi,
  type Metrics,
  type Provider,
  type ProviderWrite,
  type RequestDetail,
  type RequestRecord,
  providerPayload,
} from './api';
import { LatestSelection } from './state';
import { NAV_ITEMS } from './navigation';

const PROVIDER_KINDS = ['codex', 'openai', 'gemini', 'ollama', 'anthropic', 'openrouter', 'openai-compatible', 'deepseek', 'qwen'];
const DEFAULTS: Record<string, Partial<ProviderWrite>> = {
  codex: { base_url: null, model: 'gpt-5', api_key_env: null, supports_stream: false },
  openai: { base_url: 'https://api.openai.com/v1', model: 'gpt-5', api_key_env: 'OPENAI_API_KEY', supports_stream: true },
  gemini: { base_url: null, model: 'gemini-2.5-flash', api_key_env: 'GEMINI_API_KEY', supports_stream: false },
  ollama: { base_url: 'http://localhost:11434', model: 'llama3.2', api_key_env: null, supports_stream: false },
  anthropic: { base_url: 'https://api.anthropic.com/v1', model: 'claude-sonnet-4-5', api_key_env: 'ANTHROPIC_API_KEY', supports_stream: false },
  openrouter: { base_url: 'https://openrouter.ai/api/v1', model: 'openai/gpt-5', api_key_env: 'OPENROUTER_API_KEY', supports_stream: true },
  deepseek: { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', api_key_env: 'DEEPSEEK_API_KEY', supports_stream: true },
  qwen: { base_url: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', api_key_env: 'DASHSCOPE_API_KEY', supports_stream: true },
  'openai-compatible': { base_url: '', model: '', api_key_env: null, supports_stream: true },
};

type DesktopBridge = {
  BackendStatus?: () => Promise<{ state: string; message: string }>;
  RestartBackend?: () => Promise<void>;
};

declare global {
  interface Window {
    go?: { main?: { App?: DesktopBridge } };
  }
}

let currentAbort: AbortController | undefined;
let activePage = '';

export function renderApp(api = new GatewayApi()): void {
  const root = document.querySelector<HTMLElement>('#app');
  if (!root) return;
  root.innerHTML = `
    <div class="shell">
      <aside>
        <button class="brand" data-page="dashboard"><span>AI</span><strong>Gateway</strong></button>
        <nav>${NAV_ITEMS.map(item => `<button data-page="${item.id}">${item.label}</button>`).join('')}</nav>
        <footer><span class="status-dot"></span> Stateless by design</footer>
      </aside>
      <main id="content" aria-live="polite"></main>
    </div>`;
  root.querySelectorAll<HTMLButtonElement>('[data-page]').forEach(button => {
    button.addEventListener('click', () => void navigate(button.dataset['page'] ?? 'dashboard', api));
  });
  void navigate('dashboard', api);
}

async function navigate(page: string, api: GatewayApi): Promise<void> {
  const selectedPage = NAV_ITEMS.some(item => item.id === page) ? page : 'dashboard';
  activePage = selectedPage;
  currentAbort?.abort();
  currentAbort = undefined;
  document.querySelectorAll('[data-page]').forEach(item => item.classList.toggle('active', (item as HTMLElement).dataset['page'] === selectedPage));
  const routes: Record<string, () => Promise<void>> = {
    dashboard: () => dashboard(api),
    providers: () => providers(api),
    routing: () => routing(api),
    requests: () => requests(api),
    playground: () => playground(api),
    settings: () => settings(api),
  };
  await (routes[selectedPage] ?? routes['dashboard'])();
}

function pageIsActive(page: string, target: HTMLElement): boolean {
  return activePage === page && target.isConnected;
}

function content(): HTMLElement {
  const element = document.querySelector<HTMLElement>('#content');
  if (!element) throw new Error('Application content is unavailable.');
  return element;
}

function pageHeader(eyebrow: string, title: string, description: string): string {
  return `<header><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p>${description}</p></header>`;
}

async function dashboard(api: GatewayApi): Promise<void> {
  const target = content();
  target.innerHTML = `${pageHeader('Operations', 'Dashboard', 'Live gateway throughput and fallback health.')}<div class="state">Loading metrics…</div>`;
  try {
    const [metrics, health] = await Promise.all([api.metrics(), api.health().catch(() => ({ status: 'error', warning: 'Gateway health endpoint is unavailable.' }))]);
    if (!pageIsActive('dashboard', target)) return;
    target.innerHTML = `${pageHeader('Operations', 'Dashboard', 'Live gateway throughput and fallback health.')}${metricsMarkup(metrics)}<div class="notice">${escapeHtml(health.warning || 'Gateway online.')}</div>`;
  } catch {
    if (!pageIsActive('dashboard', target)) return;
    target.innerHTML = `${pageHeader('Operations', 'Dashboard', 'Live gateway throughput and fallback health.')}<div class="error">Metrics could not be loaded.</div>`;
  }
}

function metricsMarkup(metrics: Metrics): string {
  const values = [
    ['Total requests', metrics.total_requests],
    ['Successful', metrics.successful_requests],
    ['Failed', metrics.failed_requests],
    ['Fallbacks', metrics.fallback_requests],
    ['Average latency', `${metrics.average_latency_ms} ms`],
  ];
  return `<section class="metrics">${values.map(([label, value]) => `<article><span>${label}</span><strong>${value}</strong></article>`).join('')}</section>`;
}

async function providers(api: GatewayApi): Promise<void> {
  const target = content();
  target.innerHTML = `${pageHeader('Connections', 'Providers', 'Configure APIs, CLIs and local models with write-only credentials.')}<div class="state">Loading providers…</div>`;
  try {
    const values = await api.providers();
    if (!pageIsActive('providers', target)) return;
    renderProviders(target, api, values);
  } catch {
    if (!pageIsActive('providers', target)) return;
    target.innerHTML = `${pageHeader('Connections', 'Providers', 'Configure APIs, CLIs and local models with write-only credentials.')}<div class="error">Providers could not be loaded.</div>`;
  }
}

function renderProviders(target: HTMLElement, api: GatewayApi, values: Provider[]): void {
  target.innerHTML = `
    ${pageHeader('Connections', 'Providers', 'Configure APIs, CLIs and local models with write-only credentials.')}
    <form class="panel provider-form" id="create-provider">
      <h2>Add provider</h2>
      <div class="form-grid">
        <label>ID <input name="id" required pattern="[a-zA-Z0-9_-]+" placeholder="my-provider"></label>
        <label>Name <input name="name" required placeholder="My provider"></label>
        <label>Kind <select name="kind">${PROVIDER_KINDS.map(kind => `<option value="${kind}">${kind}</option>`).join('')}</select></label>
        <label>Model <input name="model" required></label>
        <label>Base URL <input name="base_url"></label>
        <label>Priority <input type="number" name="priority" min="0" value="100"></label>
        <label>API key (write-only) <input type="password" name="api_key" autocomplete="new-password"></label>
        <label class="toggle"><input type="checkbox" name="enabled" checked> Enabled</label>
      </div>
      <button type="submit">Create provider</button><span class="form-message"></span>
    </form>
    ${values.length ? `<section class="stack">${values.map(providerCard).join('')}</section>` : '<div class="empty">No providers configured.</div>'}`;
  const createForm = target.querySelector<HTMLFormElement>('#create-provider');
  const kind = createForm?.elements.namedItem('kind') as HTMLSelectElement | null;
  kind?.addEventListener('change', () => applyCreateDefaults(createForm, kind.value));
  applyCreateDefaults(createForm, kind?.value ?? 'codex');
  createForm?.addEventListener('submit', async event => {
    event.preventDefault();
    const message = createForm.querySelector<HTMLElement>('.form-message');
    setMessage(message, 'Saving…');
    try {
      const data = new FormData(createForm);
      await api.createProvider({
        id: String(data.get('id') ?? ''), name: String(data.get('name') ?? ''), kind: String(data.get('kind') ?? ''),
        model: String(data.get('model') ?? ''), base_url: nullable(data.get('base_url')), priority: numberValue(data.get('priority'), 100),
        api_key_env: (DEFAULTS[String(data.get('kind'))]?.api_key_env as string | null | undefined) ?? null,
        timeout_seconds: 60, supports_stream: Boolean(DEFAULTS[String(data.get('kind'))]?.supports_stream), enabled: data.get('enabled') === 'on',
        api_key: nullable(data.get('api_key')),
      });
      await providers(api);
    } catch {
      setMessage(message, 'Provider could not be created.', true);
    }
  });
  target.querySelectorAll<HTMLElement>('[data-provider]').forEach(card => wireProviderCard(card, api));
}

function providerCard(provider: Provider): string {
  const id = escapeHtml(provider.id);
  return `<article class="provider" data-provider="${id}">
    <div class="provider-heading"><h2>${escapeHtml(provider.name)}</h2><p>${escapeHtml(provider.kind)} · ${escapeHtml(provider.model)}</p></div>
    <div class="form-grid">
      <label>Name <input data-field="name" value="${escapeHtml(provider.name)}"></label>
      <label>Model <input data-field="model" value="${escapeHtml(provider.model)}" list="models-${id}"></label>
      <datalist id="models-${id}"></datalist>
      <label>Base URL <input data-field="base_url" value="${escapeHtml(provider.base_url ?? '')}"></label>
      <label>Timeout <input type="number" data-field="timeout_seconds" min="1" value="${provider.timeout_seconds}"></label>
      <label>Environment key <input data-field="api_key_env" value="${escapeHtml(provider.api_key_env ?? '')}"></label>
      <label>Priority <input type="number" data-field="priority" min="0" value="${provider.priority}"></label>
      <label>Replace API key <input type="password" data-secret autocomplete="new-password" placeholder="Write-only"></label>
      <label class="toggle"><input type="checkbox" data-field="enabled" ${provider.enabled ? 'checked' : ''}> Enabled</label>
      <label class="toggle"><input type="checkbox" data-field="supports_stream" ${provider.supports_stream ? 'checked' : ''}> Streaming</label>
    </div>
    <div class="provider-actions">
      <button data-action="save">Save configuration</button><button data-action="secret">Update key</button><button class="secondary" data-action="clear">Clear key</button>
      ${provider.kind === 'openrouter' ? '<button class="secondary" data-action="models">Load models</button>' : ''}
      <button class="secondary" data-action="health">Check health</button><button class="danger" data-action="delete">Delete</button>
    </div><div class="card-message"></div>
  </article>`;
}

function wireProviderCard(card: HTMLElement, api: GatewayApi): void {
  const id = card.dataset['provider'] ?? '';
  const existing = async (): Promise<Provider> => (await api.providers()).find(item => item.id === id) ?? Promise.reject(new Error('Provider missing'));
  card.querySelectorAll<HTMLButtonElement>('[data-action]').forEach(button => button.addEventListener('click', async () => {
    const message = card.querySelector<HTMLElement>('.card-message');
    setMessage(message, 'Working…');
    try {
      const provider = readProviderCard(card, await existing());
      switch (button.dataset['action']) {
        case 'save': await api.updateProvider(providerPayload(provider)); break;
        case 'secret': {
          const secret = card.querySelector<HTMLInputElement>('[data-secret]');
          if (!secret?.value) throw new Error('empty credential');
          await api.updateProvider(providerPayload(provider, secret.value));
          secret.value = '';
          break;
        }
        case 'clear': await api.updateProvider(providerPayload(provider, null)); break;
        case 'health': {
          const health = await api.providerHealth(id);
          setMessage(message, health.healthy ? `Healthy · ${health.detail}` : `Unavailable · ${health.detail}`, !health.healthy);
          return;
        }
        case 'models': {
          const result = await api.providerModels(id);
          const list = card.querySelector<HTMLDataListElement>('datalist');
          if (list) list.innerHTML = result.models.map(model => `<option value="${escapeHtml(model)}"></option>`).join('');
          setMessage(message, `${result.models.length} models loaded.`);
          return;
        }
        case 'delete':
          if (!window.confirm(`Delete provider ${id}?`)) return;
          await api.deleteProvider(id);
          if (pageIsActive('providers', card)) await providers(api);
          return;
      }
      setMessage(message, 'Saved.');
    } catch {
      setMessage(message, 'Operation could not be completed.', true);
    }
  }));
}

function readProviderCard(card: HTMLElement, original: Provider): Provider {
  const input = (name: string): HTMLInputElement | null => card.querySelector(`[data-field="${name}"]`);
  return {
    ...original,
    name: input('name')?.value ?? original.name,
    model: input('model')?.value ?? original.model,
    base_url: nullable(input('base_url')?.value),
    api_key_env: nullable(input('api_key_env')?.value),
    timeout_seconds: numberValue(input('timeout_seconds')?.value, original.timeout_seconds),
    priority: numberValue(input('priority')?.value, original.priority),
    enabled: input('enabled')?.checked ?? original.enabled,
    supports_stream: input('supports_stream')?.checked ?? original.supports_stream,
  };
}

function applyCreateDefaults(form: HTMLFormElement | null, kind: string): void {
  if (!form) return;
  const defaults = DEFAULTS[kind] ?? DEFAULTS['openai-compatible'];
  const model = form.elements.namedItem('model') as HTMLInputElement | null;
  const url = form.elements.namedItem('base_url') as HTMLInputElement | null;
  if (model) model.value = defaults.model ?? '';
  if (url) url.value = defaults.base_url ?? '';
}

async function routing(api: GatewayApi): Promise<void> {
  const target = content();
  target.innerHTML = `${pageHeader('Policy', 'Routing', 'Automatic routing follows enabled provider priority.')}<div class="state">Loading routing policy…</div>`;
  try {
    const value = await api.routing();
    if (!pageIsActive('routing', target)) return;
    target.innerHTML = `${pageHeader('Policy', 'Routing', 'Automatic routing follows enabled provider priority.')}<form class="panel" id="routing-form"><label class="toggle"><input type="checkbox" name="fallback" ${value.fallback_enabled ? 'checked' : ''}> Enable recoverable fallback</label><label>Maximum attempts <input type="number" name="attempts" min="1" max="20" value="${value.max_attempts}"></label><button>Save routing</button><span class="form-message"></span></form>`;
    target.querySelector<HTMLFormElement>('#routing-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget as HTMLFormElement;
      const message = form.querySelector<HTMLElement>('.form-message');
      try {
        await api.updateRouting({ fallback_enabled: (form.elements.namedItem('fallback') as HTMLInputElement).checked, max_attempts: numberValue((form.elements.namedItem('attempts') as HTMLInputElement).value, 5) });
        setMessage(message, 'Saved.');
      } catch { setMessage(message, 'Routing could not be saved.', true); }
    });
  } catch {
    if (!pageIsActive('routing', target)) return;
    target.innerHTML = `${pageHeader('Policy', 'Routing', 'Automatic routing follows enabled provider priority.')}<div class="error">Routing policy could not be loaded.</div>`;
  }
}

async function requests(api: GatewayApi): Promise<void> {
  const target = content();
  target.innerHTML = `${pageHeader('Telemetry', 'Requests', 'Operational metadata only. Prompts and responses are never stored.')}<div class="state">Loading requests…</div>`;
  try {
    const values = await api.requests();
    if (!pageIsActive('requests', target)) return;
    target.innerHTML = `${pageHeader('Telemetry', 'Requests', 'Operational metadata only. Prompts and responses are never stored.')}${requestTable(values)}<section id="request-detail"></section>`;
    const latest = new LatestSelection();
    target.querySelectorAll<HTMLButtonElement>('[data-request]').forEach(button => button.addEventListener('click', async () => {
      const id = button.dataset['request'] ?? '';
      const token = latest.select(id);
      const detail = target.querySelector<HTMLElement>('#request-detail');
      if (detail) detail.innerHTML = '<div class="state">Loading attempts…</div>';
      try {
        const value = await api.requestDetail(id);
        if (detail && latest.isCurrent(id, token)) detail.innerHTML = requestDetailMarkup(value);
      } catch {
        if (detail && latest.isCurrent(id, token)) detail.innerHTML = '<div class="error">Request attempts could not be loaded.</div>';
      }
    }));
  } catch {
    if (!pageIsActive('requests', target)) return;
    target.innerHTML = `${pageHeader('Telemetry', 'Requests', 'Operational metadata only. Prompts and responses are never stored.')}<div class="error">Requests could not be loaded.</div>`;
  }
}

function requestTable(values: RequestRecord[]): string {
  if (!values.length) return '<div class="empty">No requests have been recorded.</div>';
  return `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Provider</th><th>Status</th><th>Attempts</th><th>Latency</th><th></th></tr></thead><tbody>${values.map(request => `<tr><td>${escapeHtml(new Date(request.created_at).toLocaleString())}</td><td>${escapeHtml(request.selected_provider ?? request.requested_provider ?? 'Auto')}</td><td><span class="pill">${escapeHtml(request.status)}</span></td><td>${request.attempt_count}</td><td>${request.latency_ms} ms</td><td><button class="secondary" data-request="${escapeHtml(request.id)}">Inspect</button></td></tr>`).join('')}</tbody></table></div>`;
}

function requestDetailMarkup(detail: RequestDetail): string {
  const attempts = detail.attempts.length ? detail.attempts.map(attempt => `<article class="attempt"><div><strong>${escapeHtml(attempt.provider_name)}</strong><p>${escapeHtml(attempt.provider_id)} · ${attempt.latency_ms} ms</p></div><span class="pill">${attempt.success ? 'success' : escapeHtml(attempt.error_kind ?? 'failed')}</span><p>${attempt.error_message ? `Failure reason: ${escapeHtml(attempt.error_message)}` : ''}</p></article>`).join('') : '<div class="empty">No external attempts were created.</div>';
  return `<section class="detail panel"><h2>Request attempts</h2>${attempts}</section>`;
}

async function playground(api: GatewayApi): Promise<void> {
  const target = content();
  let values: Provider[] = [];
  try { values = await api.providers(); } catch { /* displayed below */ }
  if (!pageIsActive('playground', target)) return;
  target.innerHTML = `${pageHeader('Test bench', 'Playground', 'Run automatic or provider-specific generations, with optional streaming.')}
    <section class="playground"><form class="panel" id="playground-form"><label>Provider <select name="provider"><option value="">Auto</option>${values.filter(item => item.enabled).map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('')}</select></label><label>Prompt <textarea name="prompt" rows="12" required></textarea></label><label class="toggle"><input type="checkbox" name="stream" checked> Stream response</label><div class="provider-actions"><button>Generate</button><button type="button" class="secondary" id="cancel-generation">Cancel</button></div><div class="form-message"></div></form><article class="output"><h2>Response</h2><pre id="playground-output">Ready.</pre><p id="playground-meta"></p></article></section>`;
  const form = target.querySelector<HTMLFormElement>('#playground-form');
  target.querySelector<HTMLButtonElement>('#cancel-generation')?.addEventListener('click', () => {
    currentAbort?.abort(); currentAbort = undefined; setMessage(form?.querySelector('.form-message') ?? null, 'Cancelled.');
  });
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    currentAbort?.abort();
    const controller = new AbortController();
    currentAbort = controller;
    const output = target.querySelector<HTMLElement>('#playground-output');
    const meta = target.querySelector<HTMLElement>('#playground-meta');
    const message = form.querySelector<HTMLElement>('.form-message');
    const data = new FormData(form);
    const prompt = String(data.get('prompt') ?? '');
    const provider = nullable(data.get('provider'));
    const stream = data.get('stream') === 'on';
    if (output) output.textContent = '';
    if (meta) meta.textContent = '';
    setMessage(message, 'Generating…');
    try {
      if (stream) {
        const result = await api.streamGenerate(prompt, provider, chunk => { if (output) output.textContent += chunk; }, controller.signal);
        if (meta) meta.textContent = `${result.provider} · ${result.model} · ${result.attempts.length} attempt(s)`;
      } else {
        const result = await api.generate(prompt, provider, controller.signal);
        if (output) output.textContent = result.response;
        if (meta) meta.textContent = `${result.provider} · ${result.model} · ${result.attempts.length} attempt(s)`;
      }
      setMessage(message, 'Completed.');
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      setMessage(message, 'Generation failed. Inspect Requests for sanitized attempts.', true);
    } finally {
      if (currentAbort === controller) currentAbort = undefined;
    }
  });
}

async function settings(api: GatewayApi): Promise<void> {
  const target = content();
  target.innerHTML = `${pageHeader('Guardrails', 'Settings', 'Control data retention and prompt validation.')}<div class="state">Loading settings…</div>`;
  try {
    const value = await api.settings();
    if (!pageIsActive('settings', target)) return;
    target.innerHTML = `${pageHeader('Guardrails', 'Settings', 'Control data retention and prompt validation.')}<form class="panel" id="settings-form"><label>Telemetry retention (days) <input type="number" name="retention" min="1" value="${value.retention_days}"></label><label>Maximum prompt characters <input type="number" name="maximum" min="1" value="${value.max_prompt_chars}"></label><button>Save settings</button><span class="form-message"></span></form><div class="notice">Credentials are encrypted with a key stored separately in the local application data directory.</div>`;
    target.querySelector<HTMLFormElement>('#settings-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget as HTMLFormElement;
      const message = form.querySelector<HTMLElement>('.form-message');
      try {
        await api.updateSettings({ retention_days: numberValue((form.elements.namedItem('retention') as HTMLInputElement).value, 30), max_prompt_chars: numberValue((form.elements.namedItem('maximum') as HTMLInputElement).value, 50_000) });
        setMessage(message, 'Saved.');
      } catch { setMessage(message, 'Settings could not be saved.', true); }
    });
  } catch {
    if (!pageIsActive('settings', target)) return;
    target.innerHTML = `${pageHeader('Guardrails', 'Settings', 'Control data retention and prompt validation.')}<div class="error">Settings could not be loaded.</div>`;
  }
}

async function bootstrap(): Promise<void> {
  const root = document.querySelector<HTMLElement>('#app');
  if (!root) return;
  root.innerHTML = '<div class="startup"><div class="spinner"></div><h1>AI Gateway</h1><p>Starting the local API…</p></div>';
  const api = new GatewayApi();
  const deadline = Date.now() + 27_000;
  while (Date.now() < deadline) {
    try {
      await api.health();
      renderApp(api);
      return;
    } catch { await delay(300); }
  }
  let message = 'The local API could not be started.';
  try { message = (await window.go?.main?.App?.BackendStatus?.())?.message ?? message; } catch { /* keep safe message */ }
  root.innerHTML = `<div class="startup"><h1>AI Gateway</h1><div class="error">${escapeHtml(message)}</div><button id="retry-backend">Try again</button></div>`;
  root.querySelector<HTMLButtonElement>('#retry-backend')?.addEventListener('click', async () => {
    try {
      await window.go?.main?.App?.RestartBackend?.();
      await bootstrap();
    } catch {
      const error = root.querySelector<HTMLElement>('.error');
      if (error) error.textContent = 'The local API could not be restarted. Check the local backend log.';
    }
  });
}

function nullable(value: FormDataEntryValue | string | null | undefined): string | null {
  const normalized = String(value ?? '').trim();
  return normalized || null;
}

function numberValue(value: FormDataEntryValue | string | null | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function setMessage(target: Element | null, message: string, error = false): void {
  if (!(target instanceof HTMLElement)) return;
  target.textContent = message;
  target.className = error ? 'form-message error-text' : 'form-message success';
}

function escapeHtml(value: unknown): string {
  return String(value).replace(/[&<>'"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character] ?? character);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds));
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => void bootstrap());
else void bootstrap();
