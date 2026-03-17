# Project Context

## Background

**LendFlow** is a B2B loan origination platform for SME lenders. It replaces a legacy
Excel-based underwriting workflow with a modern, API-driven system that automates credit
decisioning, document collection, and loan packaging. The platform is used internally by
underwriters and exposed to borrowers via a white-labelled portal.

The core problem: manual underwriting takes 5–10 days and is error-prone. LendFlow targets
a 24-hour turnaround through automated data enrichment, rules-based credit scoring, and
streamlined document management.

## Key Links

- Product spec: https://docs.internal/lendflow/prd-v2
- Architecture decision record: https://docs.internal/lendflow/adr
- Figma designs: https://figma.com/lendflow-portal-v2
- Existing backend codebase: https://github.com/youorg/lendflow-api
- Data model diagram: https://docs.internal/lendflow/data-model

## Tech Decisions Already Made

- **Backend**: Python 3.12 + FastAPI (existing service, extending not replacing)
- **Database**: PostgreSQL 15 with SQLAlchemy ORM (existing schema, migrations via Alembic)
- **Auth**: OAuth 2.0 via Auth0 (company-wide standard)
- **Frontend**: React 18 + TypeScript (new, greenfield — existing portal is server-rendered Django templates)
- **Document storage**: AWS S3 with pre-signed URLs
- **Credit data enrichment**: Experian Business API (contract already signed)
- **Async tasks**: Celery + Redis for background jobs (document parsing, scoring)
- **Deployment**: AWS ECS Fargate, existing CI/CD pipeline via GitHub Actions
- **Observability**: Datadog APM + structured JSON logging

## Team Conventions

- Sprint length: 2 weeks
- Story point scale: Fibonacci (1, 2, 3, 5, 8, 13)
- Definition of Done: unit tests ≥ 80% coverage, PR reviewed by 2 engineers, deployed to staging, QA sign-off
- Branching: trunk-based with short-lived feature branches; no long-lived branches
- API-first: all new features require an OpenAPI spec before implementation begins

## Constraints

- Must remain backward-compatible with existing lendflow-api v1 REST endpoints (broker integrations depend on them)
- GDPR + FCA compliance mandatory — no PII in logs, all data encrypted at rest
- Experian API rate limit: 500 requests/hour — responses must be cached
- Go-live deadline: end of Q2 2026 (hard, regulatory sign-off tied to it)
- Must support IE11... just kidding. Chrome/Firefox/Safari latest two versions

## Out of Scope (v1)

- Mobile app — web portal only
- Self-serve borrower onboarding — brokers onboard borrowers on their behalf
- Open Banking integration — post-v1 roadmap item
- Multi-currency — GBP only for launch
- Automated loan disbursement — underwriter approval still required for fund release
