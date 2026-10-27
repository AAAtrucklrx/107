function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function createPkce(cryptoProvider: Crypto = globalThis.crypto): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64url(cryptoProvider.getRandomValues(new Uint8Array(32)));
  const digest = await cryptoProvider.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, challenge: base64url(new Uint8Array(digest)) };
}
