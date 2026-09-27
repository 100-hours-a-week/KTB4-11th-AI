import { readFile, rename, writeFile } from "node:fs/promises";
import {
  type Credential,
  type CredentialStore,
  createModels,
  type Models,
} from "@earendil-works/pi-ai";
import { openaiCodexProvider } from "@earendil-works/pi-ai/providers/openai-codex";
import type { Settings } from "./settings.ts";

const PROVIDER = "openai-codex";

// OpenAI rotates the refresh token on every refresh, so it must outlive the process.
export class FileCredentialStore implements CredentialStore {
  constructor(private readonly path: string) {}

  private async load(): Promise<Record<string, Credential>> {
    let text: string;
    try {
      text = await readFile(this.path, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
      throw error;
    }
    try {
      return JSON.parse(text);
    } catch {
      throw new Error(`credential store ${this.path} is not valid JSON`);
    }
  }

  private async save(credentials: Record<string, Credential>): Promise<void> {
    const temporary = `${this.path}.tmp`;
    await writeFile(temporary, JSON.stringify(credentials), { mode: 0o600 });
    await rename(temporary, this.path);
  }

  async read(providerId: string) {
    return (await this.load())[providerId];
  }

  async list() {
    return Object.entries(await this.load()).map(([providerId, c]) => ({
      providerId,
      type: c.type,
    }));
  }

  async modify(
    providerId: string,
    fn: (current: Credential | undefined) => Promise<Credential | undefined>,
  ) {
    const credentials = await this.load();
    const next = await fn(credentials[providerId]);
    if (next !== undefined) {
      credentials[providerId] = next;
      await this.save(credentials);
    }
    return next ?? credentials[providerId];
  }

  async delete(providerId: string) {
    const credentials = await this.load();
    delete credentials[providerId];
    await this.save(credentials);
  }
}

export async function seedCodexCredential(
  store: CredentialStore,
  settings: Pick<Settings, "openaiAccessToken" | "openaiRefreshToken" | "openaiTokenExpiresEpoch">,
): Promise<void> {
  await store.modify(PROVIDER, async (current) => {
    if (current) return undefined;
    const {
      openaiAccessToken: access,
      openaiRefreshToken: refresh,
      openaiTokenExpiresEpoch: expires,
    } = settings;
    if (!access || !refresh || expires === undefined) {
      throw new Error(
        "no stored OpenAI credential: set PORTFOLIO_BUILDER_OPENAI_ACCESS_TOKEN, PORTFOLIO_BUILDER_OPENAI_REFRESH_TOKEN and PORTFOLIO_BUILDER_OPENAI_TOKEN_EXPIRES_EPOCH",
      );
    }
    return { type: "oauth", access, refresh, expires };
  });
}

export async function createCodexModels(settings: Settings): Promise<Models> {
  const store = new FileCredentialStore(settings.credentialsPath);
  await seedCodexCredential(store, settings);
  const models = createModels({ credentials: store });
  models.setProvider(openaiCodexProvider());
  return models;
}
