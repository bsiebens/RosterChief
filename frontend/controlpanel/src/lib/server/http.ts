import http from 'node:http';
import https from 'node:https';
import { Readable } from 'node:stream';
import type { ReadableStream as NodeReadableStream } from 'node:stream/web';

/**
 * fetch() for the Django backend that really sends the Host header we set. Node's built-in fetch (undici) silently
 * replaces `Host` with the URL's host, which would make django-tenants always resolve the same club. node:http does not.
 */
export function hostFetch(url: string, init: { method?: string; headers: Headers; body?: ArrayBuffer | ReadableStream<Uint8Array>; signal?: AbortSignal }): Promise<Response> {
	return new Promise((resolve, reject) => {
		const target = new URL(url);
		const method = init.method ?? 'GET';
		const headers: Record<string, string> = {};
		init.headers.forEach((value, name) => (headers[name] = value));
		const buffered = init.body instanceof ArrayBuffer ? init.body : null;
		if (buffered?.byteLength) headers['content-length'] = String(buffered.byteLength);
		const transport = target.protocol === 'https:' ? https : http;
		const req = transport.request(target, { method, headers, signal: init.signal }, (res) => {
			const out = new Headers();
			for (let i = 0; i < res.rawHeaders.length; i += 2) out.append(res.rawHeaders[i], res.rawHeaders[i + 1]);
			const status = res.statusCode ?? 502;
			const empty = method === 'HEAD' || status === 204 || status === 205 || status === 304;
			if (empty) res.resume();
			resolve(new Response(empty ? null : (Readable.toWeb(res) as unknown as ReadableStream), { status, headers: out }));
		});
		req.on('error', reject);
		if (init.body && !(init.body instanceof ArrayBuffer)) {
			const source = Readable.fromWeb(init.body as NodeReadableStream<Uint8Array>);
			source.on('error', (error) => req.destroy(error));
			req.on('close', () => source.destroy());
			source.pipe(req);
		} else {
			req.end(buffered?.byteLength ? Buffer.from(buffered) : undefined);
		}
	});
}
