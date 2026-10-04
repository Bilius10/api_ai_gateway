import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Route, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Subscription } from 'rxjs';

import { GatewayApi, GenerateResponse, Metrics, Provider, ProviderWrite, RequestDetail, RequestRecord, StreamDone } from './api';

abstract class Loadable {
  loading = true;
  error = '';
  protected fail(error: unknown): void {
    this.loading = false;
    this.error = error instanceof Error ? error.message : 'The gateway request failed.';
  }
}

@Component({
  standalone: true,
  selector: 'app-dashboard',
  imports: [CommonModule],
  template: `
    <header><p class="eyebrow">Operations</p><h1>Dashboard</h1><p>Live gateway throughput and fallback health.</p></header>
    <div class="state" *ngIf="loading">Loading metrics…</div><div class="error" *ngIf="error">{{error}}</div>
    <section class="metrics" *ngIf="metrics">
      <article><span>Total requests</span><strong>{{metrics.total_requests}}</strong></article>
      <article><span>Successful</span><strong>{{metrics.successful_requests}}</strong></article>
      <article><span>Failed</span><strong>{{metrics.failed_requests}}</strong></article>
      <article><span>Fallbacks</span><strong>{{metrics.fallback_requests}}</strong></article>
      <article><span>Average latency</span><strong>{{metrics.average_latency_ms}} ms</strong></article>
    </section>
    <div class="notice">{{warning || 'Gateway health is being checked.'}}</div>
  `,
})
export class DashboardComponent extends Loadable implements OnInit {
  private readonly api = inject(GatewayApi);
  metrics?: Metrics;
  warning = '';
  ngOnInit(): void {
    this.api.metrics().subscribe({ next: value => { this.metrics = value; this.loading = false; }, error: error => this.fail(error) });
    this.api.health().subscribe({ next: value => this.warning = value.warning, error: () => this.warning = 'Gateway health endpoint is unavailable.' });
  }
}

@Component({
  standalone: true,
  selector: 'app-providers',
  imports: [CommonModule, FormsModule],
  template: `
    <header><p class="eyebrow">Connections</p><h1>Providers</h1><p>Create providers, manage write-only credentials, control priority, and check observed health.</p></header>
    <form class="panel provider-form" (ngSubmit)="create()">
      <h2>Add provider</h2>
      <div class="form-grid">
        <label>ID <input [(ngModel)]="draft.id" name="id" required pattern="[a-zA-Z0-9_-]+" placeholder="my-provider"></label>
        <label>Name <input [(ngModel)]="draft.name" name="name" required placeholder="My provider"></label>
        <label>Kind <select [(ngModel)]="draft.kind" name="kind" (change)="applyKindDefaults()"><option *ngFor="let kind of kinds" [value]="kind">{{kind}}</option></select></label>
        <label>Model <input [(ngModel)]="draft.model" name="model" required placeholder="model-name"></label>
        <label>Base URL <input [(ngModel)]="draft.base_url" name="baseUrl" placeholder="https://host/v1"></label>
        <label>Priority <input type="number" [(ngModel)]="draft.priority" name="priority" min="0"></label>
        <label>API key (write-only) <input type="password" [(ngModel)]="draft.api_key" name="apiKey" autocomplete="new-password" placeholder="Never displayed after save"></label>
        <label class="toggle"><input type="checkbox" [(ngModel)]="draft.enabled" name="enabled"> Enabled</label>
      </div>
      <button type="submit" [disabled]="creating">{{creating ? 'Creating…' : 'Create provider'}}</button>
      <div class="error" *ngIf="createError">{{createError}}</div>
    </form>
    <div class="state" *ngIf="loading">Loading providers…</div><div class="error" *ngIf="error">{{error}}</div>
    <div class="empty" *ngIf="!loading && !providers.length">No providers configured.</div>
    <section class="stack"><article class="provider" *ngFor="let provider of providers">
      <div><h2>{{provider.name}}</h2><p>{{provider.kind}} · {{provider.model}}</p></div>
      <ng-container *ngIf="editDraft[provider.id] as edit">
      <label>Name <input [(ngModel)]="edit.name" [name]="'name-' + provider.id"></label>
      <label>Model <input [(ngModel)]="edit.model" [name]="'model-' + provider.id" [attr.list]="'models-' + provider.id"></label>
      <datalist [id]="'models-' + provider.id"><option *ngFor="let model of modelCatalog[provider.id]" [value]="model"></option></datalist>
      <label>Base URL <input [(ngModel)]="edit.base_url" [name]="'url-' + provider.id"></label>
      <label>Timeout (seconds) <input type="number" [(ngModel)]="edit.timeout_seconds" [name]="'timeout-' + provider.id" min="1"></label>
      <label>Environment key <input [(ngModel)]="edit.api_key_env" [name]="'env-' + provider.id" placeholder="Allowlisted server-side name"></label>
      <label class="toggle"><input type="checkbox" [(ngModel)]="edit.enabled" [name]="'enabled-' + provider.id"> Enabled</label>
      <label class="toggle"><input type="checkbox" [(ngModel)]="edit.supports_stream" [name]="'stream-' + provider.id"> Streaming</label>
      <label>Priority <input type="number" [(ngModel)]="edit.priority" [name]="'priority-' + provider.id" min="0"></label>
      <label>Replace API key <input type="password" [(ngModel)]="secretDraft[provider.id]" [name]="'secret-' + provider.id" autocomplete="new-password" placeholder="Write-only"></label>
      <div class="provider-actions"><button type="button" (click)="save(provider)" [disabled]="saving[provider.id]">{{saving[provider.id] ? 'Saving…' : 'Save configuration'}}</button><button type="button" (click)="saveSecret(provider)" [disabled]="!secretDraft[provider.id]">Update key</button><button type="button" class="secondary" (click)="clearSecret(provider)">Clear key</button><button type="button" class="secondary" *ngIf="provider.kind === 'openrouter'" (click)="loadModels(provider)">Load models</button><button type="button" class="secondary" (click)="check(provider)">Check health</button><button type="button" class="danger" (click)="remove(provider)" [disabled]="deleting[provider.id]">{{deleting[provider.id] ? 'Deleting…' : 'Delete'}}</button></div>
      <div class="error" *ngIf="saveError[provider.id]">{{saveError[provider.id]}}</div>
      </ng-container>
      <span class="pill" *ngIf="health[provider.id]">{{health[provider.id]}}</span>
    </article></section>
  `,
})
export class ProvidersComponent extends Loadable implements OnInit {
  private readonly api = inject(GatewayApi);
  providers: Provider[] = [];
  editDraft: Record<string, Provider> = {};
  health: Record<string, string> = {};
  modelCatalog: Record<string, string[]> = {};
  secretDraft: Record<string, string> = {};
  deleting: Record<string, boolean> = {};
  saving: Record<string, boolean> = {};
  saveError: Record<string, string> = {};
  creating = false;
  createError = '';
  readonly kinds = ['codex', 'openai', 'gemini', 'ollama', 'anthropic', 'openrouter', 'openai-compatible', 'deepseek', 'qwen'];
  draft: ProviderWrite = this.newDraft();
  ngOnInit(): void { this.api.providers().subscribe({ next: value => { this.providers = value; this.editDraft = Object.fromEntries(value.map(provider => [provider.id, {...provider}])); this.loading = false; }, error: error => this.fail(error) }); }
  save(provider: Provider): void {
    this.saving[provider.id] = true; this.saveError[provider.id] = '';
    this.api.updateProvider({...this.editDraft[provider.id]}).subscribe({
      next: updated => { this.replaceProvider(updated); delete this.saving[provider.id]; },
      error: () => { this.saveError[provider.id] = 'Configuration could not be saved.'; delete this.saving[provider.id]; },
    });
  }
  create(): void {
    this.creating = true; this.createError = '';
    const payload = {...this.draft, api_key: this.draft.api_key?.trim() || null};
    this.api.createProvider(payload).subscribe({
      next: provider => { this.providers = [...this.providers, provider].sort((a, b) => a.priority - b.priority); this.editDraft[provider.id] = {...provider}; this.draft = this.newDraft(); this.creating = false; },
      error: () => { this.createError = 'Provider could not be created.'; this.creating = false; },
    });
  }
  saveSecret(provider: Provider): void {
    const secret = this.secretDraft[provider.id];
    if (!secret) return;
    this.api.updateProvider({...this.editDraft[provider.id], api_key: secret}).subscribe({
      next: updated => { this.replaceProvider(updated); this.secretDraft[provider.id] = ''; },
      error: () => this.saveError[provider.id] = 'Credential could not be updated.',
    });
  }
  clearSecret(provider: Provider): void {
    this.api.updateProvider({...this.editDraft[provider.id], api_key: null}).subscribe({
      next: updated => { this.replaceProvider(updated); this.secretDraft[provider.id] = ''; },
      error: () => this.saveError[provider.id] = 'Credential could not be cleared.',
    });
  }
  remove(provider: Provider): void {
    this.deleting[provider.id] = true;
    this.api.deleteProvider(provider.id).subscribe({
      next: () => { this.providers = this.providers.filter(item => item.id !== provider.id); delete this.secretDraft[provider.id]; delete this.deleting[provider.id]; },
      error: error => { delete this.deleting[provider.id]; this.fail(error); },
    });
  }
  check(provider: Provider): void { this.health[provider.id] = 'Checking…'; this.api.providerHealth(provider.id).subscribe({ next: value => this.health[provider.id] = value.healthy ? `Healthy · ${value.detail}` : `Unavailable · ${value.detail}`, error: () => this.health[provider.id] = 'Health check failed' }); }
  loadModels(provider: Provider): void { this.api.providerModels(provider.id).subscribe({ next: value => this.modelCatalog[provider.id] = value.models, error: () => this.saveError[provider.id] = 'Model catalog could not be loaded.' }); }
  applyKindDefaults(): void { this.draft = {...this.draft, ...this.defaultsFor(this.draft.kind)}; }
  private replaceProvider(updated: Provider): void { this.providers = this.providers.map(item => item.id === updated.id ? updated : item); this.editDraft[updated.id] = {...updated}; }
  private defaultsFor(kind: string): Partial<ProviderWrite> {
    const values: Record<string, Partial<ProviderWrite>> = {
      codex: {base_url: null, model: 'gpt-5', api_key_env: null, supports_stream: false},
      openai: {base_url: 'https://api.openai.com/v1', model: 'gpt-5', api_key_env: 'OPENAI_API_KEY', supports_stream: true},
      gemini: {base_url: null, model: 'gemini-2.5-flash', api_key_env: 'GEMINI_API_KEY', supports_stream: false},
      ollama: {base_url: 'http://localhost:11434', model: 'llama3.2', api_key_env: null, supports_stream: false},
      anthropic: {base_url: 'https://api.anthropic.com/v1', model: 'claude-sonnet-4-5', api_key_env: 'ANTHROPIC_API_KEY', supports_stream: false},
      openrouter: {base_url: 'https://openrouter.ai/api/v1', model: 'openai/gpt-5', api_key_env: 'OPENROUTER_API_KEY', supports_stream: true},
      deepseek: {base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', api_key_env: 'DEEPSEEK_API_KEY', supports_stream: true},
      qwen: {base_url: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', api_key_env: 'DASHSCOPE_API_KEY', supports_stream: true},
      'openai-compatible': {base_url: '', model: '', api_key_env: null, supports_stream: true},
    };
    return values[kind] ?? values['openai-compatible'];
  }
  private newDraft(): ProviderWrite { return {id: '', name: '', kind: 'openai-compatible', enabled: true, priority: 100, base_url: '', model: '', api_key_env: null, timeout_seconds: 60, supports_stream: true, api_key: ''}; }
}

@Component({
  standalone: true,
  selector: 'app-routing',
  imports: [CommonModule, FormsModule],
  template: `
    <header><p class="eyebrow">Policy</p><h1>Routing</h1><p>Automatic routing follows enabled provider priority.</p></header>
    <div class="state" *ngIf="loading">Loading routing policy…</div><div class="error" *ngIf="error">{{error}}</div>
    <form class="panel" *ngIf="!loading" (ngSubmit)="save()">
      <label class="toggle"><input type="checkbox" [(ngModel)]="value.fallback_enabled" name="fallback"> Enable recoverable fallback</label>
      <label>Maximum attempts <input type="number" [(ngModel)]="value.max_attempts" name="attempts" min="1" max="20"></label>
      <button type="submit">Save routing</button><span class="success" *ngIf="saved">Saved</span>
    </form>
  `,
})
export class RoutingComponent extends Loadable implements OnInit {
  private readonly api = inject(GatewayApi);
  value = {fallback_enabled: true, max_attempts: 5};
  saved = false;
  ngOnInit(): void { this.api.routing().subscribe({ next: value => { this.value = value; this.loading = false; }, error: error => this.fail(error) }); }
  save(): void { this.saved = false; this.api.updateRouting(this.value).subscribe({ next: () => this.saved = true, error: error => this.fail(error) }); }
}

@Component({
  standalone: true,
  selector: 'app-requests',
  imports: [CommonModule],
  template: `
    <header><p class="eyebrow">Telemetry</p><h1>Requests</h1><p>Operational metadata only. Prompts and responses are never stored.</p></header>
    <div class="state" *ngIf="loading">Loading requests…</div><div class="error" *ngIf="error">{{error}}</div>
    <div class="empty" *ngIf="!loading && !requests.length">No requests have been recorded.</div>
    <div class="table-wrap" *ngIf="requests.length"><table><thead><tr><th>Time</th><th>Provider</th><th>Status</th><th>Attempts</th><th>Latency</th><th></th></tr></thead>
      <tbody><tr *ngFor="let request of requests"><td>{{request.created_at | date:'medium'}}</td><td>{{request.selected_provider || request.requested_provider || 'Auto'}}</td><td><span class="pill">{{request.status}}</span></td><td>{{request.attempt_count}}</td><td>{{request.latency_ms}} ms</td><td><button type="button" class="secondary" (click)="inspect(request)">Inspect</button></td></tr></tbody></table></div>
    <section class="detail panel" *ngIf="detailLoading || detailError || selected">
      <h2>Request attempts</h2>
      <div class="state" *ngIf="detailLoading">Loading attempts…</div>
      <div class="error" *ngIf="detailError">{{detailError}}</div>
      <div class="empty" *ngIf="selected && !selected.attempts.length">No external attempts were created.</div>
      <article class="attempt" *ngFor="let attempt of selected?.attempts">
        <div><strong>{{attempt.provider_name}}</strong><p>{{attempt.provider_id}} · {{attempt.latency_ms}} ms</p></div>
        <span class="pill">{{attempt.success ? 'success' : attempt.error_kind}}</span>
        <p *ngIf="attempt.error_message">Failure reason: {{attempt.error_message}}</p>
      </article>
    </section>
  `,
})
export class RequestsComponent extends Loadable implements OnInit {
  private readonly api = inject(GatewayApi);
  requests: RequestRecord[] = [];
  selected?: RequestDetail;
  detailLoading = false;
  detailError = '';
  private selectedId = '';
  ngOnInit(): void { this.api.requests().subscribe({ next: value => { this.requests = value; this.loading = false; }, error: error => this.fail(error) }); }
  inspect(request: RequestRecord): void {
    this.selectedId = request.id;
    this.selected = undefined; this.detailError = ''; this.detailLoading = true;
    this.api.requestDetail(request.id).subscribe({
      next: detail => { if (this.selectedId === request.id) { this.selected = detail; this.detailLoading = false; } },
      error: () => { if (this.selectedId === request.id) { this.detailError = 'Request attempts could not be loaded.'; this.detailLoading = false; } },
    });
  }
}

@Component({
  standalone: true,
  selector: 'app-playground',
  imports: [CommonModule, FormsModule],
  template: `
    <header><p class="eyebrow">Test bench</p><h1>Playground</h1><p>Run automatic or provider-specific generations, with optional SSE.</p></header>
    <section class="playground">
      <form class="panel" (ngSubmit)="run()">
        <label>Provider <select [(ngModel)]="provider" name="provider"><option value="">Auto</option><option *ngFor="let item of providers" [value]="item.id" [disabled]="!item.enabled">{{item.name}}</option></select></label>
        <label class="toggle"><input type="checkbox" [(ngModel)]="stream" name="stream"> Stream response</label>
        <label>Prompt <textarea [(ngModel)]="prompt" name="prompt" rows="10" required placeholder="Ask the gateway…"></textarea></label>
        <button type="submit" [disabled]="loading || !prompt.trim()">{{loading ? 'Running…' : 'Generate'}}</button><button type="button" class="secondary" *ngIf="loading" (click)="cancel()">Cancel</button>
      </form>
      <article class="output"><h2>Response</h2><div class="error" *ngIf="error">{{error}}</div><p *ngIf="!output && !loading">The normalized response appears here.</p><pre *ngIf="output">{{output}}</pre><p *ngIf="result">{{result.provider}} · {{result.model}} · {{result.attempts.length}} attempt(s)</p><p *ngIf="streamResult">{{streamResult.provider}} · {{streamResult.model}} · {{streamResult.attempts.length}} attempt(s)</p></article>
    </section>
  `,
})
export class PlaygroundComponent implements OnInit, OnDestroy {
  private readonly api = inject(GatewayApi);
  private readonly changeDetector = inject(ChangeDetectorRef);
  providers: Provider[] = [];
  provider = '';
  prompt = '';
  stream = true;
  loading = false;
  error = '';
  output = '';
  result?: GenerateResponse;
  streamResult?: StreamDone;
  private abortController?: AbortController;
  private requestSubscription?: Subscription;
  private manuallyCancelled = false;
  ngOnInit(): void { this.api.providers().subscribe({ next: value => this.providers = value, error: () => this.error = 'Providers could not be loaded.' }); }
  ngOnDestroy(): void { this.cancel(); }
  run(): void {
    this.cancel(); this.manuallyCancelled = false; this.error = ''; this.output = ''; this.result = undefined; this.streamResult = undefined; this.loading = true;
    if (!this.stream) {
      this.requestSubscription = this.api.generate(this.prompt, this.provider || null).subscribe({
        next: value => { this.result = value; this.output = value.response; this.loading = false; this.changeDetector.markForCheck(); },
        error: () => { if (!this.manuallyCancelled) this.error = 'Generation failed. Inspect Requests for sanitized attempts.'; this.loading = false; this.changeDetector.markForCheck(); },
      });
      return;
    }
    void this.runStream();
  }
  cancel(): void {
    this.manuallyCancelled = true;
    this.abortController?.abort();
    this.abortController = undefined;
    this.requestSubscription?.unsubscribe();
    this.requestSubscription = undefined;
    this.loading = false;
  }
  private async runStream(): Promise<void> {
    this.abortController = new AbortController();
    try {
      const response = await fetch(`${this.api.base}/generate/stream`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt: this.prompt, provider: this.provider || null, stream: true}), signal: this.abortController.signal});
      if (!response.ok || !response.body) throw new Error('Stream failed');
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let receivedDone = false;
      while (true) {
        const item = await reader.read();
        if (item.done) { buffer += decoder.decode(); break; }
        buffer += decoder.decode(item.value, {stream: true});
        const events = buffer.split('\n\n'); buffer = events.pop() ?? '';
        for (const event of events) receivedDone = this.handleStreamEvent(event) || receivedDone;
      }
      if (buffer.trim()) receivedDone = this.handleStreamEvent(buffer) || receivedDone;
      if (!receivedDone) throw new Error('Stream ended without completion metadata');
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError') && !this.manuallyCancelled) this.error = 'Streaming failed. Inspect Requests for sanitized attempts.';
    } finally { this.abortController = undefined; this.loading = false; this.changeDetector.markForCheck(); }
  }
  private handleStreamEvent(event: string): boolean {
    const type = event.match(/^event: (.+)$/m)?.[1]; const data = event.match(/^data: (.+)$/m)?.[1];
    if (!data) return false;
    const parsed = JSON.parse(data) as Record<string, unknown>;
    if (type === 'chunk') this.output += String(parsed['text'] ?? '');
    if (type === 'error') throw new Error('Stream provider failed');
    if (type === 'done') { this.streamResult = parsed as unknown as StreamDone; return true; }
    this.changeDetector.markForCheck();
    return false;
  }
}

@Component({
  standalone: true,
  selector: 'app-settings',
  imports: [CommonModule, FormsModule],
  template: `
    <header><p class="eyebrow">Guardrails</p><h1>Settings</h1><p>Control data retention and prompt validation.</p></header>
    <div class="state" *ngIf="loading">Loading settings…</div><div class="error" *ngIf="error">{{error}}</div>
    <form class="panel" *ngIf="!loading" (ngSubmit)="save()"><label>Telemetry retention (days) <input type="number" [(ngModel)]="value.retention_days" name="retention" min="1"></label><label>Maximum prompt characters <input type="number" [(ngModel)]="value.max_prompt_chars" name="maximum" min="1"></label><button type="submit">Save settings</button><span class="success" *ngIf="saved">Saved</span></form>
    <div class="notice">Credentials are encrypted locally only when AI_GATEWAY_MASTER_KEY is supplied. The key is never stored with the data.</div>
  `,
})
export class SettingsComponent extends Loadable implements OnInit {
  private readonly api = inject(GatewayApi);
  value = {retention_days: 30, max_prompt_chars: 50000};
  saved = false;
  ngOnInit(): void { this.api.settings().subscribe({ next: value => { this.value = value; this.loading = false; }, error: error => this.fail(error) }); }
  save(): void { this.saved = false; this.api.updateSettings(this.value).subscribe({ next: () => this.saved = true, error: error => this.fail(error) }); }
}

export const routes: Route[] = [
  {path: '', pathMatch: 'full', redirectTo: 'dashboard'},
  {path: 'dashboard', component: DashboardComponent},
  {path: 'providers', component: ProvidersComponent},
  {path: 'routing', component: RoutingComponent},
  {path: 'requests', component: RequestsComponent},
  {path: 'playground', component: PlaygroundComponent},
  {path: 'settings', component: SettingsComponent},
  {path: '**', redirectTo: 'dashboard'},
];

@Component({
  standalone: true,
  selector: 'app-root',
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive],
  template: `<div class="shell"><aside><a class="brand" routerLink="/dashboard"><span>AI</span><strong>Gateway</strong></a><nav><a *ngFor="let item of nav" [routerLink]="item.path" routerLinkActive="active">{{item.label}}</a></nav><footer>Stateless by design</footer></aside><main><router-outlet /></main></div>`,
})
export class AppComponent {
  readonly nav = [
    {path: '/dashboard', label: 'Dashboard'}, {path: '/providers', label: 'Providers'},
    {path: '/routing', label: 'Routing'}, {path: '/requests', label: 'Requests'},
    {path: '/playground', label: 'Playground'}, {path: '/settings', label: 'Settings'},
  ];
}
