import '@angular/compiler';
import 'zone.js';
import 'zone.js/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideZoneChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { BrowserTestingModule, platformBrowserTesting } from '@angular/platform-browser/testing';
import { provideRouter, Router } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AppComponent, DashboardComponent, PlaygroundComponent, ProvidersComponent, RequestsComponent, routes } from './app';

TestBed.initTestEnvironment(BrowserTestingModule, platformBrowserTesting());

describe('admin data states', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({providers: [provideZoneChangeDetection(), provideHttpClient(), provideHttpClientTesting()]});
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    TestBed.resetTestingModule();
  });

  it('shows loading and then real dashboard data', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Loading metrics');
    http.expectOne('/api/metrics').flush({
      total_requests: 9, successful_requests: 8, failed_requests: 1,
      fallback_requests: 2, average_latency_ms: 42, providers: {},
    });
    http.expectOne('/api/health').flush({status: 'ok', warning: 'Trusted network only'});
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('9');
    expect(fixture.nativeElement.textContent).toContain('Trusted network only');
  });

  it('shows empty and error states', () => {
    const requestFixture = TestBed.createComponent(RequestsComponent);
    requestFixture.detectChanges();
    http.expectOne('/api/requests').flush([]);
    requestFixture.detectChanges();
    expect(requestFixture.nativeElement.textContent).toContain('No requests');

    const providerFixture = TestBed.createComponent(ProvidersComponent);
    providerFixture.detectChanges();
    http.expectOne('/api/providers').flush('failed', {status: 503, statusText: 'Offline'});
    providerFixture.detectChanges();
    expect(providerFixture.nativeElement.textContent).toContain('gateway request failed');
  });

  it('loads request attempts and visible fallback reasons on demand', () => {
    const fixture = TestBed.createComponent(RequestsComponent);
    fixture.detectChanges();
    const record = {
      id: 'request-1', requested_provider: null, selected_provider: 'backup', model: 'model',
      status: 'success', latency_ms: 37, attempt_count: 2, created_at: '2026-10-03T12:00:00Z',
    };
    http.expectOne('/api/requests').flush([record]);
    fixture.detectChanges();
    const inspect = fixture.nativeElement.querySelector('button.secondary') as HTMLButtonElement;
    inspect.click();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Loading attempts');
    http.expectOne('/api/requests/request-1').flush({request: record, attempts: [
      {provider_id: 'primary', provider_name: 'Primary', success: false, latency_ms: 20, error_kind: 'timeout', error_message: 'Provider timed out'},
      {provider_id: 'backup', provider_name: 'Backup', success: true, latency_ms: 17, error_kind: null, error_message: null},
    ]});
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Primary');
    expect(fixture.nativeElement.textContent).toContain('Failure reason: Provider timed out');
    expect(fixture.nativeElement.textContent).toContain('Backup');

    fixture.componentInstance.inspect(record);
    http.expectOne('/api/requests/request-1').flush({request: record, attempts: []});
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('No external attempts were created');

    fixture.componentInstance.inspect(record);
    http.expectOne('/api/requests/request-1').flush('failed', {status: 503, statusText: 'Offline'});
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Request attempts could not be loaded');
  });

  it('creates, updates write-only credentials, and deletes providers without displaying secrets', () => {
    const fixture = TestBed.createComponent(ProvidersComponent);
    fixture.detectChanges();
    http.expectOne('/api/providers').flush([]);
    const component = fixture.componentInstance;
    component.draft = {
      id: 'custom', name: 'Custom', kind: 'openai-compatible', enabled: true, priority: 5,
      base_url: 'http://localhost:9999/v1', model: 'custom-model', api_key_env: null,
      timeout_seconds: 30, supports_stream: true, api_key: 'create-secret',
    };
    component.create();
    const create = http.expectOne('/api/providers');
    expect(create.request.method).toBe('POST');
    expect(create.request.body.api_key).toBe('create-secret');
    const saved = {...component.draft};
    delete saved.api_key;
    create.flush(saved);
    fixture.detectChanges();
    expect(component.draft.api_key).toBe('');
    expect(fixture.nativeElement.textContent).toContain('Custom');
    expect(fixture.nativeElement.textContent).not.toContain('create-secret');

    component.secretDraft['custom'] = 'replacement-secret';
    component.saveSecret(component.providers[0]);
    const update = http.expectOne('/api/providers/custom');
    expect(update.request.method).toBe('PUT');
    expect(update.request.body.api_key).toBe('replacement-secret');
    update.flush(saved);
    expect(component.secretDraft['custom']).toBe('');
    expect(fixture.nativeElement.textContent).not.toContain('replacement-secret');

    component.clearSecret(component.providers[0]);
    const clear = http.expectOne('/api/providers/custom');
    expect(clear.request.body.api_key).toBeNull();
    clear.flush(saved);

    component.loadModels(component.providers[0]);
    http.expectOne('/api/providers/custom/models').flush({provider: 'custom', models: ['custom-model', 'other-model']});
    expect(component.modelCatalog['custom']).toEqual(['custom-model', 'other-model']);

    component.remove(component.providers[0]);
    const remove = http.expectOne('/api/providers/custom');
    expect(remove.request.method).toBe('DELETE');
    remove.flush(null, {status: 204, statusText: 'No Content'});
    expect(component.providers).toEqual([]);
  });

  it('keeps the latest request selection when responses arrive out of order', () => {
    const fixture = TestBed.createComponent(RequestsComponent);
    fixture.detectChanges();
    const first = {id: 'first', requested_provider: null, selected_provider: null, model: null, status: 'failed', latency_ms: 1, attempt_count: 1, created_at: '2026-10-03T12:00:00Z'};
    const second = {...first, id: 'second', selected_provider: 'backup', status: 'success'};
    http.expectOne('/api/requests').flush([first, second]);
    fixture.componentInstance.inspect(first);
    const firstRequest = http.expectOne('/api/requests/first');
    fixture.componentInstance.inspect(second);
    const secondRequest = http.expectOne('/api/requests/second');
    secondRequest.flush({request: second, attempts: []});
    firstRequest.flush({request: first, attempts: [{provider_id: 'old', provider_name: 'Old', success: false, latency_ms: 1, error_kind: 'offline', error_message: 'Provider unavailable'}]});
    fixture.detectChanges();
    expect(fixture.componentInstance.selected?.request.id).toBe('second');
    expect(fixture.nativeElement.textContent).not.toContain('Old');
  });

  it('handles non-stream and completed, truncated, and cancelled streams', async () => {
    const fixture = TestBed.createComponent(PlaygroundComponent);
    fixture.detectChanges();
    http.expectOne('/api/providers').flush([]);
    const component = fixture.componentInstance;
    component.prompt = 'hello'; component.stream = false; component.run();
    http.expectOne('/api/generate').flush({request_id: 'r1', provider: 'p', model: 'm', response: 'answer', attempts: []});
    expect(component.output).toBe('answer');

    const complete = new Response('event: chunk\ndata: {"text":"streamed"}\n\nevent: done\ndata: {"request_id":"r2","provider":"p2","model":"m2","attempts":[]}\n\n', {status: 200, headers: {'Content-Type': 'text/event-stream'}});
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => complete;
    component.stream = true; component.run(); await fixture.whenStable();
    expect(component.output).toBe('streamed'); expect(component.streamResult?.provider).toBe('p2');

    globalThis.fetch = async () => new Response('event: chunk\ndata: {"text":"partial"}\n\n', {status: 200});
    component.run(); await fixture.whenStable();
    expect(component.error).toContain('Streaming failed');

    globalThis.fetch = (_input, init) => new Promise((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
    component.run(); component.cancel(); await fixture.whenStable();
    expect(component.loading).toBe(false); expect(component.error).toBe('');
    globalThis.fetch = originalFetch;
  });
});

describe('admin routes', () => {
  it('contains all six operational screens', () => {
    expect(routes.filter(route => route.component).map(route => route.path)).toEqual([
      'dashboard', 'providers', 'routing', 'requests', 'playground', 'settings',
    ]);
  });

  it('renders the root navigation and activates a routed screen', async () => {
    TestBed.configureTestingModule({providers: [provideZoneChangeDetection(), provideHttpClient(), provideHttpClientTesting(), provideRouter(routes)]});
    const localHttp = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('nav a').length).toBe(6);
    await TestBed.inject(Router).navigateByUrl('/providers');
    fixture.detectChanges();
    localHttp.expectOne('/api/providers').flush([]);
    await fixture.whenStable(); fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Add provider');
    localHttp.verify();
    TestBed.resetTestingModule();
  });
});
