# Output & Phase-by-Phase Review

Reference for the scrum-planner skill — read this when scrum-agent output is ready and you need to present results.

## Phase-by-Phase Review

**Do NOT dump the entire plan at once.** Present each phase one at a time, pausing for user review.

### Phase 1: Features

> *_Phase 1 of 4: Features_*
>
> 1. *{feature.name}* — {feature.description}
> 2. *{feature.name}* — {feature.description}
>
> ✅ *Accept* — move to stories
> ✏️ *Edit* — tell me what to change
> 🔄 *Regenerate* — re-run with more context

If user edits: apply changes, show updated list, ask again. If substantial edit, re-run CLI.

### Phase 2: User Stories

> *_Phase 2 of 4: User Stories_*
>
> *Feature: {feature.name}*
>
> 1. *{story.title}* ({story.story_points} pts)
>    {story.description}
>    _ACs:_ • Given {given}, When {when}, Then {then}
>
> ✅ *Accept* · ✏️ *Edit* · 🔄 *Regenerate*

Show one feature at a time if there are many.

### Phase 3: Task Breakdown

> *_Phase 3 of 4: Tasks_*
>
> *Story: {story.title}*
> 1. *{task.title}* — {task.description}
>    _{task.discipline} · {task.estimate_hours}h_
>
> ✅ *Accept* · ✏️ *Edit* · ⏭️ *Skip details*

### Phase 4: Sprint Plan

> *_Phase 4 of 4: Sprint Plan_*
>
> *Sprint {number}: {name}*
> Capacity: {capacity} pts · Committed: {committed} pts
> • {story.title} ({points} pts)
>
> ✅ *Accept* · ✏️ *Edit* · 🔄 *Regenerate*

### After All Phases Accepted

**Immediately and automatically create the Slack Canvas** — do not ask, do not offer options first. Go straight to the Final Plan Output section below and run the canvas script.

After the canvas is created, post this summary in thread:

> 📋 *{project.name}* — {N} epics · {N} stories · {N} tasks · {N} sprints
>
> 🚀 *Sprint plan finalized! See the Canvas above ☝️*
> • *Team:* {team_size} engineers · {sprint_length}-week sprints
> • *Velocity:* {velocity} pts/sprint
> • *Total effort:* {total_points} story points
>
> 🎯 Want me to push this to Jira?

### Jira Push

Check if configured: `grep -q "JIRA_BASE_URL" ~/.scrum-agent/.env 2>/dev/null`
- Configured → `scrum-agent --resume latest --export-only`
- Not configured → tell user to run `scrum-agent --setup`

## Final Plan Output

Try Canvas first, fall back to threaded messages.

> ⚠️ **IMPORTANT:** Never use the OpenClaw `canvas` tool — it requires a paired device and will not work here.
> Always use the Python script below to create a **Slack Canvas** via the Slack API.

### Canvas (preferred)
Use the canvas script to create the Slack Canvas — do not use the OpenClaw canvas tool, do not attempt direct API calls yourself.

1. Write the final plan to a temp file:
```bash
cat > /tmp/scrum_plan_canvas.md << 'EOF'
{full markdown plan}
EOF
```

2. Run the canvas script with the channel ID from the current conversation:
```bash
python3 ~/.openclaw/workspace/skills/scrum-planner/scripts/canvas.py \
  create-channel-canvas \
  --channel {CHANNEL_ID} \
  --content @/tmp/scrum_plan_canvas.md
```

The script tries `canvases.create` + `canvases.access.set` and handles all Slack API details. It reads the bot token from `~/.openclaw/openclaw.json` automatically.

Post summary in thread after canvas is created: `📋 *Sprint plan ready* — see the Canvas above ☝️`

Diagnostics commands:
```bash
scrum-agent --version 2>/dev/null || echo "unknown"
grep -E '^(LLM_PROVIDER|LLM_MODEL)=' ~/.scrum-agent/.env 2>/dev/null || echo "defaults"
```

**Never include API keys or tokens.**

### Threaded Messages (fallback)
Post each section as a separate thread reply (under 50 blocks each): Project Summary, Features & Stories, Task Breakdown, Sprint Plan, Diagnostics.

### File Upload (last resort)
Format as Markdown, upload as `.md` file attachment.

## Error Handling

- **Non-zero exit:** "Check `~/.scrum-agent/.env` has valid credentials and run `scrum-agent --setup`"
- **Timeout (>5 min):** "Try simplifying the description or reducing sprint count"
- **Empty output:** "Add more detail — tech stack, team size, or goals"
- **Not found:** "Install with `pip install 'scrum-agent[bedrock]'` then run `scrum-agent --setup`"
