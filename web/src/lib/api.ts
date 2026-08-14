import { z } from "zod";

import { ApiError } from "@/lib/schemas";

export class HttpError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
  }
}

export async function apiFetcher<T>(
  url: string,
  schema: z.ZodType<T>,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch {
    throw new HttpError(0, "API unreachable - is dashboard.py running?");
  }

  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const parsed = ApiError.parse(await res.json());
      message = parsed.error;
    } catch {
      /* keep default message */
    }
    throw new HttpError(res.status, message);
  }

  const data = await res.json();
  const parsed = schema.safeParse(data);
  if (!parsed.success) {
    throw new HttpError(
      500,
      `Invalid API response for ${url}: ${parsed.error.issues
        .map((i) => `${i.path.join(".")} ${i.message}`)
        .join("; ")}`,
    );
  }
  return parsed.data;
}
