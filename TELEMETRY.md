# Telemetry — What We Collect

scrum-agent includes **opt-in anonymous telemetry** to help us improve planning quality. It is **disabled by default** and must be explicitly enabled.

## Enabling / Disabling

```bash
# Enable
export SCRUM_AGENT_TELEMETRY=true

# Disable (default)
export SCRUM_AGENT_TELEMETRY=false
# or simply don't set the variable
```

## What We Collect

When telemetry is enabled, the following **anonymized, structural data** is sent after each completed session:

### Session Metadata
| Field | Example | Purpose |
|-------|---------|---------|
| `event_id` | `a3f91b2c-...` | Unique event ID (random UUID, not tied to user) |
| `timestamp` | `2026-03-23T12:00:00Z` | When the session completed |
| `agent_version` | `1.0.0` | Which version of scrum-agent |
| `platform` | `Darwin` | OS (macOS/Linux/Windows) |
| `python_version` | `3.12.1` | Python version |
| `llm_provider` | `anthropic` | Which LLM provider was used |

### Project Patterns (no names or descriptions)
| Field | Example | Purpose |
|-------|---------|---------|
| `project.type` | `greenfield` | Project type classification |
| `project.tech_stack` | `["Next.js", "FastAPI"]` | Technologies chosen |
| `project.integrations` | `["Stripe", "Auth0"]` | Third-party integrations |
| `project.sprint_length_weeks` | `2` | Sprint duration |
| `project.target_sprints` | `4` | Planned sprint count |
| `project.goal_count` | `3` | Number of goals (not the goals themselves) |
| `project.constraint_count` | `2` | Number of constraints |
| `project.risk_count` | `1` | Number of risks |
| `project.skip_features` | `false` | Whether features were skipped (small project) |
| `project.prompt_quality_grade` | `A` | Questionnaire completeness grade |

### Intake Patterns
| Field | Example | Purpose |
|-------|---------|---------|
| `intake.questions_answered` | `22` | How many questions were answered |
| `intake.total_questions` | `30` | Total available questions |
| `intake.mode` | `smart` | Intake mode used |
| `intake.team_size` | `5` | Team size (for velocity calibration) |

### Artifact Counts
| Field | Example | Purpose |
|-------|---------|---------|
| `counts.features` | `4` | Number of features generated |
| `counts.stories` | `16` | Number of user stories |
| `counts.tasks` | `42` | Number of tasks |
| `counts.sprints` | `3` | Number of sprints |
| `counts.total_story_points` | `58` | Total story points |

### Distributions (for better estimation models)
| Field | Example | Purpose |
|-------|---------|---------|
| `point_distribution` | `{"1": 2, "2": 5, "3": 4, "5": 3, "8": 2}` | Story point spread |
| `discipline_distribution` | `{"FRONTEND": 6, "BACKEND": 8, "FULLSTACK": 2}` | Discipline breakdown |

### Structural Patterns (shapes, not content)

**Features** — per feature:
- Priority level (critical/high/medium/low)
- Title length and description length (character counts, not actual text)

**Stories** — per story:
- Story points (Fibonacci value)
- Priority level
- Discipline (frontend/backend/fullstack/etc.)
- Number of acceptance criteria
- Feature it belongs to
- Number of applicable Definition of Done items

**Tasks** — per task:
- Label (code/documentation/infrastructure/testing)
- Whether it has a test plan
- Whether it has an AI prompt
- Which story it belongs to

**Sprints** — per sprint:
- Capacity points
- Number of stories assigned
- Total points allocated

### Human Feedback Signal
| Field | Example | Purpose |
|-------|---------|---------|
| `review_decisions` | `{"features": "accept", "stories": "edit"}` | Accept/edit/reject at each stage |

## What We Do NOT Collect

- **No project names or descriptions**
- **No actual text content** (goals, stories, tasks, acceptance criteria)
- **No source code**
- **No API keys or credentials**
- **No user names, emails, or IP addresses**
- **No file paths or repository URLs**
- **No Jira/GitHub/Confluence data**

## How It Works

1. On session completion, the telemetry module builds an anonymized payload
2. The payload is POST'd to our collection endpoint with a 3-second timeout
3. If the request fails for any reason, it fails silently — **never blocks or crashes the app**
4. Data is stored in an S3 bucket partitioned by date
5. We periodically analyse aggregate patterns to improve prompt quality, estimation accuracy, and decomposition strategies

## Data Retention

- Raw telemetry is stored indefinitely for trend analysis
- Data is never sold or shared with third parties
- Data is used exclusively to improve scrum-agent

## Custom Endpoint

If you want to collect telemetry for your own analysis:

```bash
export SCRUM_AGENT_TELEMETRY=true
export SCRUM_AGENT_TELEMETRY_URL=https://your-endpoint.example.com/collect
```

## Questions?

Open an issue at [github.com/omardin14/scrum-planning-ai-agent](https://github.com/omardin14/scrum-planning-ai-agent/issues).
