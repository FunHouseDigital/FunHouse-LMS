import { describe, it, expect, vi } from 'vitest';
import { ApiError, ContainerApiClient, UnauthorizedError, isAllowedBaseUrl } from './client';

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe('ContainerApiClient HTTPS enforcement (Req 17.4)', () => {
  it('accepts https base URLs', () => {
    expect(() => new ContainerApiClient({ baseUrl: 'https://api.funhouse.example' })).not.toThrow();
  });

  it('rejects plain http base URLs for non-localhost hosts', () => {
    expect(() => new ContainerApiClient({ baseUrl: 'http://api.funhouse.example' })).toThrow(
      /non-HTTPS/i,
    );
  });

  it('allows http://localhost and loopback for dev/tests (documented exception)', () => {
    expect(() => new ContainerApiClient({ baseUrl: 'http://localhost:8000' })).not.toThrow();
    expect(() => new ContainerApiClient({ baseUrl: 'http://127.0.0.1:8000' })).not.toThrow();
  });

  it('isAllowedBaseUrl matches the documented policy', () => {
    expect(isAllowedBaseUrl('https://x.example')).toBe(true);
    expect(isAllowedBaseUrl('http://localhost')).toBe(true);
    expect(isAllowedBaseUrl('http://127.0.0.1:3000')).toBe(true);
    expect(isAllowedBaseUrl('http://example.com')).toBe(false);
    expect(isAllowedBaseUrl('not-a-url')).toBe(false);
  });
});

describe('ContainerApiClient bearer attachment (Req 1.5)', () => {
  it('attaches Authorization: Bearer <token> when a token is available', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, []));
    const client = new ContainerApiClient({
      baseUrl: 'https://api.funhouse.example',
      getToken: () => 'jwt-token-123',
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    await client.getPlayers();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.funhouse.example/players');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer jwt-token-123');
  });

  it('omits the Authorization header when no token is available', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, []));
    const client = new ContainerApiClient({
      baseUrl: 'https://api.funhouse.example',
      getToken: () => null,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    await client.getAlerts();

    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it('sends the login request body without requiring a token', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, {
        access_token: 'tok',
        token_type: 'bearer',
        expires_at: '2099-01-01T00:00:00.000Z',
      }),
    );
    const client = new ContainerApiClient({
      baseUrl: 'https://api.funhouse.example',
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    const res = await client.login('loyiso', 'secret');
    expect(res.access_token).toBe('tok');

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.funhouse.example/auth/login');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ identifier: 'loyiso', password: 'secret' });
  });
});

describe('ContainerApiClient 401 surfacing (Req 1.7)', () => {
  it('throws UnauthorizedError distinctly on a 401 response', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(401, { detail: 'invalid' }));
    const client = new ContainerApiClient({
      baseUrl: 'https://api.funhouse.example',
      getToken: () => 'expired',
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    await expect(client.getPlayers()).rejects.toBeInstanceOf(UnauthorizedError);
  });

  it('throws ApiError (not UnauthorizedError) for other non-2xx responses', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(500, { detail: 'boom' }));
    const client = new ContainerApiClient({
      baseUrl: 'https://api.funhouse.example',
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    const err = await client.getPlayers().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).not.toBeInstanceOf(UnauthorizedError);
    expect((err as ApiError).status).toBe(500);
  });

  it('POST /sync sends the actions batch and returns per-action results (Req 5)', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, {
        results: [{ client_id: 'a', entity: 'session', status: 'applied', record_id: 'r1', reason: null }],
      }),
    );
    const client = new ContainerApiClient({
      baseUrl: 'https://api.funhouse.example',
      getToken: () => 'tok',
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    const result = await client.sync([
      { client_id: 'a', entity: 'session', created_at: '2024-01-01T00:00:00.000Z', payload: {} },
    ]);

    expect(result.results[0].status).toBe('applied');
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.funhouse.example/sync');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string).actions).toHaveLength(1);
  });
});
