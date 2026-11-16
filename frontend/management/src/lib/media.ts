/** An uploaded file's address as the browser can reach it: the API hands out absolute URLs on its own host, the app serves /media itself. */
export function mediaPath(url: string): string {
	try {
		const parsed = new URL(url);
		return parsed.pathname.startsWith('/media/') ? parsed.pathname + parsed.search : url;
	} catch {
		return url;
	}
}
