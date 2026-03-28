async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (typeof response.text !== "function") {
    return response.json() as Promise<T>;
  }

  const raw = await response.text();
  if (!raw.trim()) {
    throw new Error(`Empty response body (${response.status} ${response.statusText})`);
  }

  try {
    return JSON.parse(raw) as T;
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`Invalid JSON response (${response.status} ${response.statusText}): ${detail}`);
  }
}

export async function apiGet<T>(url: string): Promise<T> {
  const response = await fetch(url);
  return parseJsonResponse<T>(response);
}

export async function apiPost<T>(url: string, payload: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<T>(response);
}
