# CLI Invocation & Generation

Reference for the scrum-planner skill — read this when you're ready to generate the plan (after intake is confirmed).

## SCRUM.md Generation

Generate a temporary `SCRUM.md` file with the collected answers. Use this exact structure — the section headers and keywords are parsed by the agent's keyword extraction:

```markdown
## Background
{Q1 description}
Project type: {Q2 — use exact keyword: "greenfield", "existing codebase", or "hybrid"}

## Goals
{Q3 answer — the problem and who it serves}

## Definition of Done
{Q4 answer — the end-state}

## Tech Decisions Already Made
{Q11 answer — include specific framework, language, database, and infrastructure names}

## Team Conventions
Sprint length: {Q8} weeks
Target sprints: {Q10}

## Constraints
{Any constraints from optional question, or "None specified"}

## Out of Scope
{Any exclusions from optional question, or "None specified"}
```

**Keyword rules** — include these exact terms when the user mentions them:
- **Project type:** "greenfield", "existing codebase", "hybrid", "refactor", "migrate", "legacy", "rewrite", "from scratch", "new project"
- **Services:** "stripe", "auth0", "firebase", "twilio", "sendgrid", "segment", "launchdarkly", "datadog", "pagerduty", "sentry", "okta", "plaid", "algolia", "cloudflare", "vercel"
- **Infrastructure:** "kubernetes", "k8s", "microservices", "serverless", "lambda", "aws", "gcp", "azure", "docker", "monolith", "on-premise", "terraform", "cloudformation", "ecs", "eks"

## CLI Invocation

**Always run in the background** to avoid exec timeouts, then poll for completion.

### Step 1: Launch in background

```bash
TMPDIR=$(mktemp -d) && cd "$TMPDIR" && cat > SCRUM.md << 'SCRUMEOF'
{generated SCRUM.md content}
SCRUMEOF
nohup scrum-agent --non-interactive \
  --description "{Q1 answer — keep under 500 characters}" \
  --team-size {Q6} \
  --sprint-length {Q8 as integer: 1, 2, 3, or 4} \
  --output json \
  </dev/null \
  > /tmp/scrum-output.json 2>/tmp/scrum-stderr.log &
echo "PID:$! TMPDIR:$TMPDIR"
```

Tell the user:
> "☕ Generating your sprint plan — this runs through 5 AI phases and takes 2-5 minutes. I'll check progress and let you know when it's ready."

### Step 2: Poll for progress

Check every 30-60 seconds:

```bash
kill -0 {PID} 2>/dev/null && echo "RUNNING" || echo "DONE"
grep -E "✓|took|failed" /tmp/scrum-stderr.log 2>/dev/null | tail -5
```

Update the user with progress as phases complete.

### Step 3: Read output when done

```bash
cat /tmp/scrum-output.json
```

If empty or error, check: `tail -20 /tmp/scrum-stderr.log`

**Important:**
- SCRUM.md must be in CWD
- `--description` under 500 chars (long text goes in SCRUM.md `## Background`)
- `</dev/null` prevents interactive prompts
- Sprint length is an integer (1, 2, 3, or 4)
