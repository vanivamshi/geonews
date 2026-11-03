# GeoNews — System Flowcharts

Geo-aware news intelligence agent.

---

## 1. Overall System Architecture

```
+-----------------------------------------------------------------------+
|                                 USERS                                 |
|     Web app user                            Email / Telegram digest   |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                     FRONTEND  (Next.js on Vercel)                     |
|  User: feed + stories | chat (streaming SSE) | settings + geo map     |
|  Admin: trace viewer | eval dashboard | labeling UI | pipeline health |
+-----------------------------------+-----------------------------------+
                                    |  REST + SSE
                                    v
+-----------------------------------------------------------------------+
|                       FASTAPI GATEWAY (Python)                        |
|                   auth | rate limit | event capture                   |
+-----------+-----------------------+-----------------------+-----------+
            |                       |                       |
            v                       v                       v
+---------------------+  +---------------------+  +---------------------+
|    AGENT RUNTIME    |  |   FEED + RANKING    |  |     EVENTS API      |
| orchestrator +      |  | candidates -> ranker|  | clicks, dwell,      |
| subagents           |  | (heuristic/LightGBM)|  | ratings, queries    |
+----+-----------+----+  +----------+----------+  +----------+----------+
     |           |                  |                        |
     v           v                  |                        |
+-----------+ +---------------+     |                        |
| MCP       | | LLM ROUTER    |     |                        |
| SERVERS   | |---------------|     |                        |
|-----------| | Sonnet 5.5    |     |                        |
| news-mcp  | | Haiku 5.5     |     |                        |
| geo-mcp   | | LoRA (vLLM)   |     |                        |
| memory-mcp| | fallback      |     |                        |
+-----+-----+ +---------------+     |                        |
      |                             |                        |
      v                             v                        v
+-----------------------------------------------------------------------+
|                              DATA LAYER                               |
|  Postgres + pgvector + PostGIS  |  Redis (cache, queue)  |  R2 / S3   |
+-----------------------------------------------------------------------+
                                    ^
                                    |
+-----------------------------------------------------------------------+
|                          BACKGROUND WORKERS                           |
|  Ingestion  |  Digest builder  |  Nightly evals  |  Training export   |
+-----------------------------------------------------------------------+

  Observability (all layers): OpenTelemetry --> Langfuse + Grafana + Sentry
```

---

## 2. Ingestion Pipeline

```
 +-----------+   +-----------+   +-------------+   +-------------+
 | RSS feeds |   |   GDELT   |   | Hacker News |   |  Open data  |
 +-----+-----+   +-----+-----+   +------+------+   +------+------+
       |               |                |                 |
       +---------------+-------+--------+-----------------+
                               |
                               v
                 +----------------------------+
                 |    Redis Streams queue     |
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+     +----------------+
                 |  Fetch + extract           |---->| Raw HTML -> R2 |
                 |  (trafilatura)             |     +----------------+
                 +-------------+--------------+
                               |
                               v
                 < Seen before? (URL hash) >------ yes ----> skip
                               |
                               | no
                               v
                 +----------------------------+
                 |  Near-dup check (MinHash)  |
                 |  -> cluster into Stories   |
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+
                 |  NER: places, orgs, people |
                 +-------------+--------------+
                               |
                               v
                 < Geocode in cache? >------- no ----> Nominatim lookup
                               |                              |
                               | yes                          | (cache it)
                               v                              |
                 +----------------------------+               |
                 |  Attach geo point/region   |<--------------+
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+
                 |  Classify (LoRA model):    |
                 |  topic, locality, news-    |
                 |  worthiness                |
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+
                 |  Embed -> pgvector         |
                 +----------------------------+

   any stage fails ----> Dead-letter stream ----> retry / quarantine
```

---

## 3. Chat Query Flow (Multi-Agent)

```
                 +----------------------------+
                 |     User asks a question   |
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+
                 | Gateway: auth, rate limit, |
                 | log event                  |
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+
                 | ORCHESTRATOR (Sonnet 5.5)  |
                 | load context:              |
                 |  - cached prompt + tools   |
                 |  - user profile summary    |
                 |  - top-k recent history    |
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+
          +----->|  Plan query (+ load skill  |
          |      |  if needed)                |
          |      +-------------+--------------+
          |                    |
          |          +---------+---------+
          |          |                   |
          |          v                   v
          | +----------------+  +----------------+
          | |   RETRIEVER    |  |   RESEARCHER   |
          | |   (Haiku 5.5)  |  |   (Haiku 5.5)  |
          | | search_stories |  | get_article    |
          | | regions_near   |  | extract claims |
          | +--------+-------+  +--------+-------+
          |          |                   |
          |          +---------+---------+
          |                    |  compressed findings
          |                    v  (claims + citation IDs)
          |      +----------------------------+
          |      | Orchestrator drafts answer |
          |      +-------------+--------------+
          |                    |
          |                    v
          |      +----------------------------+
          |      | VERIFIER (Haiku 5.5):      |
          |      | check each claim vs source |
          |      +-------------+--------------+
          |                    |
          |                    v
          +-- no --< all claims supported? >-- no, budget out --+
                               |                                |
                               | yes                            v
                               |                  +----------------------------+
                               |                  | Degrade: answer only the   |
                               |                  | supported claims + flag    |
                               |                  | uncertainty                |
                               |                  +-------------+--------------+
                               |                                |
                               v                                |
                 +----------------------------+                 |
                 |  Final cited answer        |<----------------+
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+
                 |  Stream to UI (SSE)        |
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+
                 |  User feedback: thumbs,    |
                 |  follow-ups, dwell time    |
                 |  -> events table           |
                 +----------------------------+

  Guardrails on every run: token budget | max steps | tool timeouts | cost cap
```

---

## 4. Daily Briefing Flow

```
                 +----------------------------+
                 | Scheduler (daily, per TZ)  |
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+
                 | Candidate generation:      |
                 |  geo radius + follows +    |
                 |  interest vector match     |
                 +-------------+--------------+
                               |
                               v
                 +----------------------------+
          +----->| Ranker                     |
          |      |  v1 heuristic / v2 LightGBM|
          |      +-------------+--------------+
          |                    |
          |                    v
          |      +----------------------------+
          |      | Log shown items + features |
          |      +-------------+--------------+
          |                    |
          |                    v
          |      +----------------------------+
          |      | Briefing writer            |
          |      | (Sonnet 5.5, Batch API)    |
          |      | + citation check           |
          |      +-------------+--------------+
          |                    |
          |       +------------+------------+
          |       |            |            |
          |       v            v            v
          |   +-------+   +----------+   +-----+
          |   | Email |   | Telegram |   | Web |
          |   +---+---+   +----+-----+   +--+--+
          |       |            |            |
          |       +------------+------------+
          |                    |
          |                    v
          |      +----------------------------+
          +------| Clicks, opens, ratings     |
                 | -> events table            |
                 +----------------------------+
```

---

## 5. Eval + Training Flywheel

```
   +-----------------------------------------+
   |  Production traces + user events        |<-----------------------+
   +--------------------+--------------------+                        |
                        |                                             |
                        v                                             |
   +-----------------------------------------+                        |
   |  Nightly sampler -> LLM-as-judge        |                        |
   |  (faithfulness, coverage, usefulness)   |                        |
   +--------------------+--------------------+                        |
                        |                                             |
                        v                                             |
                 < low score? >----- no -----> Eval dashboard         |
                        |                                             |
                        | yes                                         |
                        v                                             |
   +-----------------------------------------+       +-------------+  |
   |  Labeling UI (human review)             |------>| Judge       |  |
   +---------+---------------------+---------+       | calibration |  |
             |                     |                 | (kappa) ->  |  |
             v                     v                 | back to     |  |
   +-------------------+  +-------------------+      | judge       |  |
   | Golden eval sets  |  | Pairwise prefs    |      +-------------+  |
   | (versioned)       |  | (chosen/rejected) |                       |
   +----+---------+----+  +---------+---------+                       |
        |         |                 |                                 |
        |         v                 v                                 |
        |   +-----------+    +-------------+                          |
        |   | LoRA train|    | DPO train   |                          |
        |   +-----+-----+    +------+------+                          |
        |         |                 |                                 |
        |         +--------+--------+                                 |
        |                  |                                          |
        |                  v                                          |
        |  +-------------------------------+                          |
        |  | Compare vs prompted Haiku:    |                          |
        |  | accuracy / cost / latency     |                          |
        |  +---------------+---------------+                          |
        |                  |                                          |
        |                  v                                          |
        |  +-------------------------------+                          |
        |  | Update LLM Router routing     |                          |
        |  +---------------+---------------+                          |
        |                  |                                          |
        v                  |                                          |
   +-------------------+   |                                          |
   | CI eval gate on   |   |                                          |
   | prompt/model/tool |   |                                          |
   | PRs; regression = |   |                                          |
   | block merge       |   |                                          |
   +----+--------------+   |                                          |
        |                  |                                          |
        | pass             |                                          |
        v                  v                                          |
   +-----------------------------------------+                        |
   |  Deploy behind feature flag -> A/B test |                        |
   +--------------------+--------------------+                        |
                        |                                             |
                        +---------------------------------------------+
```

---

## 6. Deployment + CI/CD

```
  +-------------------+
  | Push / PR (GitHub)|
  +---------+---------+
            |
            v
  +-------------------+
  |  GitHub Actions   |
  |  - lint + tests   |
  |  - eval subset    |
  +---------+---------+
            |
            v
      < all pass? >------ no -----> fail PR
            |
            | yes
            v
  +-------------------+
  |   Docker build    |
  +---------+---------+
            |
            +---------------------+-----------------------+
            |                     |                       |
            v                     v                       v
+--------------------+  +--------------------+  +--------------------+
| Fly.io / Railway   |  | Vercel             |  | Modal              |
| API, workers, MCP  |  | Next.js frontend   |  | vLLM LoRA model    |
+-----------+--------+  +--------------------+  +--------------------+
            |
            +-----------------+---------------------+
            |                 |                     |
            v                 v                     v
     +-------------+   +-------------+  +----------------------------+
     | Neon        |   | Upstash     |  | OTel -> Langfuse, Grafana, |
     | Postgres    |   | Redis       |  | Sentry                     |
     +-------------+   +-------------+  +----------------------------+
```

---

## 7. Scale Evolution

```
+-------------------------+     +-------------------------+
| v0: 1-50 users          |     | v1: ~1k users           |
|-------------------------|     |-------------------------|
| Monolith FastAPI        |---->| Split workers per stage |
| One Postgres + pgvector |     | Read replicas           |
| Redis Streams + cron    |     | Redis feed/geo cache    |
|                         |     | Batch API for digests   |
+-------------------------+     +------------+------------+
                                             |
                                             v
+-------------------------+     +-------------------------+
| v3: 1M+ users           |     | v2: ~100k users         |
|-------------------------|     |-------------------------|
| ClickHouse warehouse    |<----| Kafka / Redpanda ingest |
| Online/offline rankers  |     | Qdrant vector DB        |
| Multi-region            |     | Per-region candidates   |
| Per-tenant cost limits  |     | Feature store           |
|                         |     | Autoscaled vLLM         |
+-------------------------+     +-------------------------+
```

---

## 8. Build Phases

```
+----------------------------------------------------------+
| PHASE 1  (Wk 1-2)   Ingestion + DB + feed + email digest |
|                     to 5 real users                      |
+----------------------------+-----------------------------+
                             |
                             v
+----------------------------------------------------------+
| PHASE 2  (Wk 3-4)   Chat agent + MCP tools + citations   |
|                     + streaming UI                       |
+----------------------------+-----------------------------+
                             |
                             v
+----------------------------------------------------------+
| PHASE 3  (Wk 5)     Tracing, cost dashboards, event logs |
+----------------------------+-----------------------------+
                             |
                             v
+----------------------------------------------------------+
| PHASE 4  (Wk 6-7)   Eval harness, golden sets, judge     |
|                     calibration, CI eval gate            |
+----------------------------+-----------------------------+
                             |
                             v
+----------------------------------------------------------+
| PHASE 5  (Wk 8-9)   Multi-agent: researcher + verifier,  |
|                     skills, memory                       |
+----------------------------+-----------------------------+
                             |
                             v
+----------------------------------------------------------+
| PHASE 6  (Wk 10-11) LoRA classifier + DPO summarizer     |
|                     + LLM router + cost/accuracy report  |
+----------------------------+-----------------------------+
                             |
                             v
+----------------------------------------------------------+
| PHASE 7  (Wk 12)    Learned ranker, scale write-up,      |
|                     blog posts, public repo              |
+----------------------------------------------------------+
```
