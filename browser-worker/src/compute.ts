/**
 * compute.ts — Operació determinista simulada (SHA-256).
 *
 * Representació canònica de l'entrada:
 *   1. Ordenar claus JSON alfabèticament
 *   2. Codificar com a JSON compacte (sense espais)
 *   3. Codificar en UTF-8
 *   4. SHA-256
 *
 * La mateixa entrada ha de produir exactament el mateix hash al:
 *   - worker Python (rc3_state.simulated_compute)
 *   - worker navegador (aquest fitxer)
 *   - coordinador (rc3_coordinator.py)
 */

/**
 * Serialitza un objecte JSON de forma canònica (claus ordenades, compacte).
 */
export function jsonCanonical(obj: Record<string, unknown>): string {
  const keys = Object.keys(obj).sort();
  const parts = keys.map((k) => {
    const v = obj[k];
    return `${JSON.stringify(k)}:${jsonValue(v)}`;
  });
  return '{' + parts.join(',') + '}';
}

function jsonValue(v: unknown): string {
  if (v === null) return 'null';
  if (v === undefined) return 'null';
  if (typeof v === 'number') return Number.isInteger(v) ? v.toString() : v.toFixed(6);
  if (typeof v === 'string') return JSON.stringify(v);
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (Array.isArray(v)) return '[' + v.map(jsonValue).join(',') + ']';
  if (typeof v === 'object') {
    const obj = v as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    const parts = keys.map((k) => `${JSON.stringify(k)}:${jsonValue(obj[k])}`);
    return '{' + parts.join(',') + '}';
  }
  return JSON.stringify(v);
}

/**
 * Calcula SHA-256 d'una string (UTF-8).
 */
export async function sha256(input: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(input);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Operació determinista simulada: calcula SHA-256 de la representació
 * canònica de { input, seed, protocol_version }.
 */
export async function simulatedCompute(
  input: string,
  seed: number,
  protocolVersion: string = 'rc3-protocol-v1',
): Promise<string> {
  const canonical = jsonCanonical({
    input,
    seed,
    protocol_version: protocolVersion,
  });
  return sha256(canonical);
}
