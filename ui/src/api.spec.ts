import { describe, expect, it, vi } from 'vitest';

import { GatewayApi, SSEParser, providerPayload, type Provider } from './api';

const provider: Provider = {
  id: 'openai', name: 'OpenAI', kind: 'openai', enabled: true, priority: 1,
  base_url: 'https://api.openai.com/v1', model: 'gpt-5', api_key_env: 'OPENAI_API_KEY',
  timeout_seconds: 60, supports_stream: true,
};

describe('GatewayApi', () => {
  it('uses the exposed local API and preserves the generate contract', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ request_id: '1', provider: 'openai', model: 'gpt-5', response: 'answer', attempts: [] }), { status: 200, headers: { 'content-type': 'application/json' } }));
    const api = new GatewayApi('http://127.0.0.1:8000/api', transport);
    const result = await api.generate('question', null);
    expect(result.response).toBe('answer');
    expect(transport).toHaveBeenCalledWith('http://127.0.0.1:8000/api/generate', expect.objectContaining({ method: 'POST', body: JSON.stringify({ prompt: 'question', provider: null }) }));
  });

  it('keeps credentials write-only unless an explicit replacement is supplied', () => {
    expect(providerPayload(provider)).not.toHaveProperty('api_key');
    expect(providerPayload(provider, 'secret')).toHaveProperty('api_key', 'secret');
    expect(providerPayload(provider, null)).toHaveProperty('api_key', null);
  });

  it('parses fragmented SSE events', () => {
    const parser = new SSEParser();
    expect(parser.feed('event: chunk\ndata: {"text":"hel')).toEqual([]);
    expect(parser.feed('lo"}\n\nevent: done\ndata: {"request_id":"1","provider":"p","model":"m","attempts":[]}\n\n')).toEqual([
      { type: 'chunk', data: { text: 'hello' } },
      { type: 'done', data: { request_id: '1', provider: 'p', model: 'm', attempts: [] } },
    ]);
  });

  it('parses CRLF delimiters split across network chunks', () => {
    const parser = new SSEParser();
    expect(parser.feed('event: chunk\r\ndata: {"text":"ok"}\r')).toEqual([]);
    expect(parser.feed('\n\r\n')).toEqual([{ type: 'chunk', data: { text: 'ok' } }]);
  });

  it('accumulates streaming chunks and requires completion metadata', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(streamResponse([
      'event: chunk\ndata: {"text":"one"}\n\n',
      'event: chunk\ndata: {"text":" two"}\n\nevent: done\ndata: {"request_id":"1","provider":"p","model":"m","attempts":[]}\n\n',
    ]));
    const chunks: string[] = [];
    const result = await new GatewayApi('http://local/api', transport).streamGenerate('q', null, chunk => chunks.push(chunk));
    expect(chunks.join('')).toBe('one two');
    expect(result.provider).toBe('p');
  });

  it('rejects a truncated stream without a done event', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(streamResponse(['event: chunk\ndata: {"text":"partial"}\n\n']));
    await expect(new GatewayApi('http://local/api', transport).streamGenerate('q', null, () => undefined)).rejects.toThrow('completion metadata');
  });

  it('surfaces structured HTTP failure without response content', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response('upstream secret', { status: 503 }));
    await expect(new GatewayApi('http://local/api', transport).health()).rejects.toThrow('503');
    await expect(new GatewayApi('http://local/api', transport).health()).rejects.not.toThrow('upstream secret');
  });
});

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}
