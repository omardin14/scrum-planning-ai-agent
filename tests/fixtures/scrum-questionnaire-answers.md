# Scrum Agent — Project Intake Questionnaire

Fill in your answers after the `> ` on each question.
Multi-line answers are supported — start each line with `> `.
To skip a question, leave the `> ` line blank or write `> skip`.

**Essential questions** (must be answered for best results): Q1, Q2, Q3, Q4, Q6, Q11, Q15

---

## Phase 1: Project Context

**Q1.** What is the project? Describe it in a few sentences, or point me to a repo/doc.
> LendFlow — a B2B loan origination platform for SME lenders. We're modernising a manual
> Excel-based underwriting workflow into an API-driven system with automated credit decisioning,
> document collection, and loan packaging. Underwriters use an internal portal; borrowers
> interact via a white-labelled broker-facing portal.

**Q2.** Is this a greenfield project or are you building on an existing codebase?
> Hybrid — the backend API (Python/FastAPI) already exists and we're extending it.
> The frontend portal is greenfield (replacing Django server-rendered templates with React).

**Q3.** What problem does this project solve? Who are the end users?
> Manual underwriting takes 5–10 days and is error-prone. LendFlow automates data enrichment,
> credit scoring, and document management to target a 24-hour decision turnaround.
> End users: (1) internal underwriters who review and approve loans, (2) brokers who submit
> loan applications on behalf of SME borrowers.

**Q4.** What does "done" look like? What's the end-state you're targeting?
> A fully functional loan origination workflow: broker submits application → automated Experian
> credit check → document upload and parsing → underwriter review dashboard → loan decision
> and offer letter generation. All accessible via the new React portal with Auth0 SSO.

**Q5.** Are there any hard deadlines or milestones?
> Yes — go-live end of Q2 2026 (hard deadline, tied to FCA regulatory sign-off).
> Internal milestone: staging-ready by end of April 2026 for QA and compliance review.

---

## Phase 2: Team & Capacity

**Q6.** How many engineers are working on this?
> 6

**Q7.** What are the roles on the team? (e.g., 2 backend, 1 frontend, 1 fullstack)
> 3 backend (Python/FastAPI), 2 frontend (React/TypeScript), 1 fullstack (owns auth + DevOps)

**Q8.** How long are your sprints? (e.g., 1 week, 2 weeks)
> 2 weeks

**Q9.** Do you have a known velocity from previous sprints? If yes, what is it?
> Yes — approximately 42 story points per sprint across the team (last 3 sprints average)

**Q10.** How many sprints are you targeting to complete this project?
> 6 sprints (12 weeks) to hit the Q2 go-live

---

## Phase 3: Technical Context

**Q11.** What is the tech stack? (languages, frameworks, databases, infra)
> Backend: Python 3.12, FastAPI, SQLAlchemy, Alembic, Celery, Redis
> Frontend: React 18, TypeScript, Vite, TailwindCSS
> Database: PostgreSQL 15
> Auth: Auth0 (OAuth 2.0)
> Storage: AWS S3
> Infra: AWS ECS Fargate, GitHub Actions CI/CD, Datadog APM

**Q12.** Are there any existing APIs, services, or third-party integrations involved?
> - Experian Business API for credit data enrichment (contract signed, 500 req/hour limit)
> - Auth0 for SSO
> - AWS S3 for document storage (pre-signed URLs)
> - Existing lendflow-api v1 REST endpoints (must remain backward-compatible — brokers depend on them)

**Q13.** Are there any architectural constraints or decisions already made? (e.g., must use microservices, must deploy to AWS)
> - Must stay on AWS (existing infrastructure, company policy)
> - API-first: all features need an OpenAPI spec before implementation
> - Experian responses must be cached (rate limit constraint)
> - All data encrypted at rest (FCA + GDPR requirement)
> - No PII in logs

**Q14.** Is there any existing documentation, PRDs, or design docs I should reference?
> - PRD v2: https://docs.internal/lendflow/prd-v2
> - Architecture ADRs: https://docs.internal/lendflow/adr
> - Figma designs: https://figma.com/lendflow-portal-v2
> - Data model: https://docs.internal/lendflow/data-model

---

## Phase 3a: Codebase Context

**Q15.** Does the project have an existing codebase, or is this a new build?
> Existing backend codebase being extended; new React frontend

**Q16.** Where is the code hosted? (GitHub, Azure DevOps, GitLab, Bitbucket, local only)
> GitHub

**Q17.** Can you share the repo URL(s)? (the agent can connect and scan the repo for context)
> https://github.com/youorg/lendflow-api

**Q18.** How is the repo structured? (monorepo, multi-repo, microservices, monolith)
> Two repos: lendflow-api (backend monolith) and lendflow-portal (new React frontend)

**Q19.** Is there an existing CI/CD pipeline or deployment setup?
> Yes — GitHub Actions for CI (lint, test, build). ECS Fargate for deployment.
> Staging and production environments. Deploys triggered on merge to main.

**Q20.** Is there any known technical debt? (legacy code, outdated dependencies, areas needing refactoring)
> - Legacy Django views still serving some internal pages — need migrating to FastAPI
> - The credit scoring module is a monolithic 2000-line file with no tests
> - SQLAlchemy models use raw SQL in several places — needs ORM cleanup

---

## Phase 4: Risks & Unknowns

**Q21.** Are there any areas of the project you're uncertain or worried about? (technical risk, unclear requirements, dependencies on other teams)
> - Experian API integration complexity — sandbox access only, production credentials pending
> - Document parsing accuracy for varied PDF formats (bank statements, tax returns)
> - Auth0 B2B multi-tenancy configuration for white-labelling — unfamiliar territory
> - Credit scoring rules definition — still being finalised by the credit risk team

**Q22.** Are there any known blockers or dependencies on external teams/systems?
> - Credit risk team must finalise scoring rules before the decisioning engine can be built (ETA: 2 weeks)
> - Legal must approve the offer letter template before generation feature can be tested
> - Experian production API credentials — contract signed but onboarding takes ~3 weeks

**Q23.** Is there anything that's explicitly out of scope?
> - Mobile app (web portal only)
> - Self-serve borrower onboarding (broker-mediated only)
> - Open Banking integration (post-v1)
> - Multi-currency (GBP only)
> - Automated loan disbursement (underwriter approval still required)

---

## Phase 5: Preferences & Process

**Q24.** How do you want stories estimated? (Fibonacci story points, T-shirt sizes, or no estimates)
> Fibonacci story points (1, 2, 3, 5, 8, 13)

**Q25.** Do you have a Definition of Done the team follows?
> Yes: unit tests with ≥ 80% coverage, PR reviewed by 2 engineers, deployed to staging,
> QA sign-off, no open P0/P1 bugs, OpenAPI spec updated if endpoints changed

**Q26.** Do you want the output pushed to Jira, exported as Markdown, or both?
> Both — export Markdown for review, then push to Jira once confirmed

---
