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

> 📋 *{project.name}* — {N} epics · {N} stories · {N} tasks · {N} sprints
>
> 🚀 *Sprint plan finalized!*
> • *Team:* {team_size} engineers · {sprint_length}-week sprints
> • *Velocity:* {velocity} pts/sprint
> • *Total effort:* {total_points} story points
>
> What's next?
> 1. Show full plan as a single document
> 2. Export as Markdown
> 3. Drill into any story or feature
> 4. 🎯 Push to Jira

### Jira Push

Check if configured: `grep -q "JIRA_BASE_URL" ~/.scrum-agent/.env 2>/dev/null`
- Configured → `scrum-agent --resume latest --export-only`
- Not configured → tell user to run `scrum-agent --setup`

## Final Plan Output

Try Canvas first, fall back to threaded messages.

### Canvas (preferred)
If `canvases:write` scope is available, create a Canvas with: header, project summary, features & stories, task breakdown, sprint plan, diagnostics.

Post summary in thread: `📋 *Sprint plan ready* — see the Canvas above ☝️`

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
