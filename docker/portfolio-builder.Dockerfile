FROM oven/bun:1.4.2-slim

WORKDIR /app
COPY services/portfolio-builder/package.json services/portfolio-builder/bun.lock ./
RUN bun install --frozen-lockfile --production

COPY services/portfolio-builder/src ./src

# The credential store lives on a volume at /data so rotated OpenAI tokens survive runs.
RUN mkdir /data && chown bun:bun /data
USER bun
CMD ["bun", "src/main.ts"]
