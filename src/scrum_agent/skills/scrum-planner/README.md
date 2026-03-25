# scrum-planner — OpenClaw Skill

An OpenClaw skill that conducts conversational scrum planning intake and generates full project plans using `scrum-agent`.

## Prerequisites

- **scrum-agent** installed on the OpenClaw instance:
  ```bash
  pip install 'scrum-agent[bedrock]'   # for AWS Lightsail with Bedrock
  # or
  pip install scrum-agent              # for direct Anthropic API
  ```
- **Setup wizard** completed: `scrum-agent --setup`
- **Verified** headless mode works:
  ```bash
  scrum-agent --non-interactive --description "Build a todo app" --output json
  ```

## Installation

The skill is bundled with scrum-agent. Install it with a single command:

```bash
scrum-agent --install-skill
```

This copies SKILL.md, README.md, and the `references/` folder to the OpenClaw skills directory and sandbox workspace.

To install to a custom directory:

```bash
scrum-agent --install-skill /path/to/openclaw/skills
```

Alternatively, copy manually:

```bash
# From your local machine
scp -r skills/scrum-planner/ user@lightsail-ip:~/.openclaw/skills/scrum-planner/
```

## How It Works

The skill conducts a 7-question conversational intake, then invokes `scrum-agent --non-interactive` with the collected answers.

### Question-to-CLI Mapping

| Question | Maps to | Notes |
|----------|---------|-------|
| Q1: Project description | `--description` arg + SCRUM.md `## Background` | Short summary for arg, full text in SCRUM.md |
| Q2: Project type | SCRUM.md `## Background` | Exact keywords: "greenfield", "existing codebase", "hybrid" |
| Q3+Q4: Problem/users/done | SCRUM.md `## Goals` + `## Definition of Done` | Merged into one conversational question |
| Q6: Team size | `--team-size` arg | Integer |
| Q8: Sprint length | `--sprint-length` arg | Integer (1-4) |
| Q10: Target sprints | SCRUM.md `## Team Conventions` | Text value |
| Q11: Tech stack | SCRUM.md `## Tech Decisions Already Made` | Include framework, DB, infra names |
| Optional context | SCRUM.md `## Constraints` + `## Out of Scope` | Free-form extras |

### Skill File Structure

```
skills/scrum-planner/
  SKILL.md                          — Core skill (intake, questions, confirmation)
  README.md                         — This file
  references/
    cli-and-generation.md           — SCRUM.md template, keyword rules, CLI invocation
    output-and-review.md            — Phase-by-phase review, Canvas output, error handling
```

The SKILL.md is kept small (~300 lines) so it doesn't exceed Bedrock's context on every conversation turn. The `references/` files are only loaded when needed (after confirmation and after generation).

### Flow

1. User describes their project (or says "just plan it" for quick mode)
2. Skill extracts what it can, asks follow-up questions with numbered choices
3. Shows confirmation summary with answer sources and defaults
4. On confirmation: reads `references/cli-and-generation.md`, generates SCRUM.md, runs `scrum-agent` in background
5. Polls progress, updates user as phases complete (~3-5 minutes)
6. Reads `references/output-and-review.md`, presents results phase-by-phase (features → stories → tasks → sprints) with accept/edit/regenerate
7. After all phases accepted: summary + offer to export as Canvas, Markdown, or push to Jira

## Example Conversation

```
User: I need to plan a mobile banking app

Skill: Great! Let me help you plan that out. Is this a greenfield project,
       building on an existing codebase, or a hybrid?

User: Greenfield

Skill: What problem does this solve, who are the end users, and what does
       'done' look like?

User: Our retail customers need mobile check deposit and P2P transfers.
      Done = both features live in app stores with 99.9% uptime.

Skill: How many engineers are working on this?

User: 6

Skill: How long are your sprints? (1, 2, 3, or 4 weeks — default is 2)

User: 2

Skill: What's the tech stack?

User: React Native, Node.js, PostgreSQL, deployed on AWS ECS

Skill: How many sprints are you targeting? (1-2, 3-5, 6-10, 10+, or let
       the agent decide)

User: 6-10

Skill: Anything else? Constraints, integrations, risks, out of scope?

User: Must integrate with Plaid for bank connections. PCI compliance required.
      No crypto features in v1.

Skill: Here's what I've got:

       | Question        | Answer                                          |
       |-----------------|------------------------------------------------|
       | Project         | Mobile banking app                              |
       | Type            | Greenfield                                      |
       | Problem & done  | Retail customers need mobile check deposit...   |
       | Team size       | 6 engineers                                     |
       | Sprint length   | 2 weeks                                         |
       | Tech stack      | React Native, Node.js, PostgreSQL, AWS ECS      |
       | Target sprints  | 6-10 sprints                                    |
       | Extra context   | Plaid integration, PCI compliance, no crypto    |

       Does this look right?

User: Yes

Skill: [runs scrum-agent, presents results]

       Generated 5 features, 18 stories, 47 tasks across 8 sprints.
       ...
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `scrum-agent: command not found` | Install: `pip install 'scrum-agent[bedrock]'` |
| Auth/credential errors | Run `scrum-agent --setup` to reconfigure API keys |
| Empty JSON output | Add more detail to the project description |
| Timeout (>5 min) | Simplify description or reduce target sprints |
| Missing features in output | Ensure SCRUM.md has specific tech stack and constraint keywords |
| Bedrock throttling in Slack | SKILL.md too large — ensure `references/` files are installed (not inlined) |
| Canvas not created | Add `canvases:read` and `canvases:write` scopes to Slack App, reinstall app |

## Related

- [scrum-agent README](../../README.md) — full CLI docs and deployment guide
- [SCRUM.md.example](../../SCRUM.md.example) — template for the generated SCRUM.md
- [Lightsail deployment guide](../../README.md#deploy-on-aws-lightsail-openclaw) — full setup instructions
