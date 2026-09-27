import { expect, test } from "bun:test";
import { mkdtemp, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { FileCredentialStore, seedCodexCredential } from "../src/credentials.ts";

const tokens = {
  openaiAccessToken: "access",
  openaiRefreshToken: "refresh",
  openaiTokenExpiresEpoch: 1790000000000,
};

async function tempPath() {
  return join(await mkdtemp(join(tmpdir(), "pb-cred-")), "auth.json");
}

test("seeds an empty store from the environment tokens, readable only by the owner", async () => {
  const path = await tempPath();

  await seedCodexCredential(new FileCredentialStore(path), tokens);

  expect(await new FileCredentialStore(path).read("openai-codex")).toEqual({
    type: "oauth",
    access: "access",
    refresh: "refresh",
    expires: 1790000000000,
  });
  expect((await stat(path)).mode & 0o777).toBe(0o600);
});

test("never overwrites a stored, possibly rotated credential", async () => {
  const path = await tempPath();
  const store = new FileCredentialStore(path);
  await store.modify("openai-codex", async () => ({
    type: "oauth",
    access: "rotated",
    refresh: "rotated",
    expires: 1,
  }));

  await seedCodexCredential(store, tokens);

  expect((await store.read("openai-codex")) as { access: string }).toMatchObject({
    access: "rotated",
  });
});

test("an empty store without environment tokens is an error", async () => {
  const store = new FileCredentialStore(await tempPath());

  await expect(seedCodexCredential(store, {})).rejects.toThrow(
    "PORTFOLIO_BUILDER_OPENAI_ACCESS_TOKEN",
  );
});
