"""Curated golden dataset project descriptions for regression testing.

Each dataset is a dict with:
- ``description``: Rich project description (used as Q1 answer)
- ``answers``: Full Q1–Q26 answer set for the questionnaire
- ``expected``: Structural expectations for artifact validation

These datasets drive the full pipeline (analyzer → epics → stories → tasks →
sprints) and assert structural properties of the output — not exact text,
since LLM outputs vary across runs.
"""

from __future__ import annotations

from scrum_agent.agent.state import TOTAL_QUESTIONS, QuestionnaireState

# ---------------------------------------------------------------------------
# Dataset 1: Todo App (greenfield, small team)
# ---------------------------------------------------------------------------

TODO_APP = {
    "description": (
        "Build a full-stack todo application with React frontend and FastAPI backend. "
        "Users can create, edit, delete, and organize tasks into projects. "
        "Supports user authentication, real-time updates via WebSocket, and "
        "a responsive dashboard."
    ),
    "answers": {
        1: "Full-stack todo application with React frontend and FastAPI backend",
        2: "Greenfield",
        3: "Task management, user authentication, real-time updates",
        4: "Individual developers, small teams",
        5: "Deployed to production with CI/CD, 1000+ daily active users",
        6: "3 engineers",
        7: "1 frontend, 1 backend, 1 fullstack",
        8: "2-week sprints",
        9: "3 sprints (6 weeks total)",
        10: "15 points per sprint",
        11: "React, TypeScript, FastAPI, Python, PostgreSQL",
        12: "REST API with WebSocket for real-time updates",
        13: "GitHub OAuth for authentication",
        14: "Must be deployable on AWS, GDPR compliant for EU users",
        15: "New build — no existing code",
        16: "Monorepo with frontend/ and backend/ directories",
        17: "No repo URL yet",
        18: "Jest for frontend, pytest for backend",
        19: "GitHub Actions",
        20: "README and API docs via Swagger",
        21: "WebSocket reliability across poor connections",
        22: "GitHub OAuth API stability",
        23: "Performance at scale unknown",
        24: "Fibonacci story points (1, 2, 3, 5, 8)",
        25: "kebab-case for URLs, camelCase for JS, snake_case for Python",
        26: "Daily standups, retrospectives after each sprint",
    },
    "expected": {
        "min_epics": 3,
        "max_epics": 6,
        "min_stories_per_epic": 2,
        "max_stories_per_epic": 5,
        "min_tasks_per_story": 2,
        "max_tasks_per_story": 5,
        "min_sprints": 1,
        "max_sprints": 4,
        "target_sprints": 3,
        "team_size": 3,
    },
}

# ---------------------------------------------------------------------------
# Dataset 2: SaaS Platform (hybrid, medium team)
# ---------------------------------------------------------------------------

SAAS_PLATFORM = {
    "description": (
        "Build a B2B SaaS platform for invoice management. Existing legacy system "
        "needs migration to modern stack. Multi-tenant architecture with role-based "
        "access control, PDF generation, email notifications, and Stripe integration "
        "for subscription billing."
    ),
    "answers": {
        1: "B2B SaaS invoice management platform with multi-tenant architecture",
        2: "Hybrid — migrating legacy monolith to modern microservices",
        3: "Invoice CRUD, PDF generation, Stripe billing, RBAC, email notifications",
        4: "Accountants, finance managers, small business owners",
        5: "Full production launch replacing the legacy system within Q3",
        6: "5 engineers",
        7: "2 backend, 1 frontend, 1 DevOps, 1 QA",
        8: "2-week sprints",
        9: "5 sprints (10 weeks)",
        10: "25 points per sprint",
        11: "Next.js, TypeScript, Node.js, PostgreSQL, Redis",
        12: "Microservices with API gateway, event-driven messaging via RabbitMQ",
        13: "Stripe API, SendGrid, AWS S3 for document storage",
        14: "SOC 2 compliance required, 99.9% uptime SLA, must support 10K concurrent users",
        15: "Existing legacy PHP monolith to be migrated incrementally",
        16: "Separate repos per service, shared library repo",
        17: "https://github.com/example/invoice-platform",
        18: "Jest, Cypress for E2E, pytest for microservices",
        19: "GitHub Actions with ArgoCD for Kubernetes deployments",
        20: "Confluence wiki, OpenAPI specs for each service",
        21: "Data migration from legacy MySQL to PostgreSQL",
        22: "Stripe API rate limits during high-volume billing periods",
        23: "Legacy system quirks not documented — hidden business rules",
        24: "Fibonacci story points (1, 2, 3, 5, 8)",
        25: "PascalCase for components, camelCase for functions, SCREAMING_SNAKE for constants",
        26: "Sprint reviews with stakeholders, automated regression testing gate",
    },
    "expected": {
        "min_epics": 3,
        "max_epics": 6,
        "min_stories_per_epic": 2,
        "max_stories_per_epic": 5,
        "min_tasks_per_story": 2,
        "max_tasks_per_story": 5,
        "min_sprints": 1,  # fallback may pack all into 1 sprint when velocity is high
        "max_sprints": 6,
        "target_sprints": 5,
        "team_size": 5,
    },
}

# ---------------------------------------------------------------------------
# Dataset 3: Mobile App (greenfield, small team)
# ---------------------------------------------------------------------------

MOBILE_APP = {
    "description": (
        "Build a cross-platform mobile app for restaurant reservations. "
        "Users browse restaurants, view menus, make reservations, and pay deposits. "
        "Restaurant owners get a management dashboard for table availability "
        "and reservation approvals."
    ),
    "answers": {
        1: "Cross-platform mobile app for restaurant reservations with owner dashboard",
        2: "Greenfield",
        3: "Restaurant browsing, reservation booking, deposit payments, owner dashboard",
        4: "Diners looking for restaurants, restaurant owners managing bookings",
        5: "MVP launched on iOS and Android app stores within 3 months",
        6: "4 engineers",
        7: "2 mobile, 1 backend, 1 design",
        8: "2-week sprints",
        9: "4 sprints (8 weeks for MVP)",
        10: "20 points per sprint",
        11: "React Native, TypeScript, Node.js, Express, MongoDB",
        12: "REST API with push notification service",
        13: "Stripe for payments, Google Maps API, Firebase for push notifications",
        14: "Must work offline for menu browsing, < 3s load time on 3G",
        15: "New build — no existing code",
        16: "Monorepo: mobile/, api/, shared/",
        17: "No repo yet — will create after sprint planning",
        18: "React Native Testing Library, Supertest for API",
        19: "Fastlane for mobile CI, GitHub Actions for API",
        20: "Figma designs (linked), API docs in Postman",
        21: "Payment processing reliability and PCI compliance",
        22: "Google Maps API cost at scale, Apple/Google review process delays",
        23: "Restaurant owner adoption rate — may need training materials",
        24: "Fibonacci story points (1, 2, 3, 5, 8)",
        25: "camelCase everywhere, components in PascalCase",
        26: "Bi-weekly demos to restaurant partners, design reviews before implementation",
    },
    "expected": {
        "min_epics": 3,
        "max_epics": 6,
        "min_stories_per_epic": 2,
        "max_stories_per_epic": 5,
        "min_tasks_per_story": 2,
        "max_tasks_per_story": 5,
        "min_sprints": 2,
        "max_sprints": 5,
        "target_sprints": 4,
        "team_size": 4,
    },
}

# ---------------------------------------------------------------------------
# Dataset 4: API Gateway (infrastructure, DevOps team)
# ---------------------------------------------------------------------------

API_GATEWAY = {
    "description": (
        "Build a custom API gateway to replace Kong. Needs rate limiting, "
        "JWT validation, request routing, circuit breaker, request/response "
        "logging, and a configuration dashboard. Must handle 50K requests/sec."
    ),
    "answers": {
        1: "Custom API gateway with rate limiting, JWT auth, circuit breaker, and config dashboard",
        2: "Greenfield — replacing existing Kong setup",
        3: "Rate limiting, JWT validation, routing, circuit breaker, logging, config UI",
        4: "Platform engineers, DevOps teams, API consumers",
        5: "Production gateway handling 50K req/sec with <10ms p99 latency",
        6: "3 engineers",
        7: "2 backend/infra, 1 frontend (dashboard)",
        8: "2-week sprints",
        9: "3 sprints (6 weeks)",
        10: "15 points per sprint",
        11: "Go, Redis, PostgreSQL, React (dashboard), Prometheus",
        12: "Reverse proxy architecture with plugin system for middleware",
        13: "Prometheus for metrics, Grafana for dashboards, HashiCorp Vault for secrets",
        14: "Must handle 50K req/sec, zero-downtime deployments, < 10ms added latency",
        15: "New build",
        16: "Single Go module with internal packages",
        17: "No repo yet",
        18: "Go testing package, k6 for load testing",
        19: "GitHub Actions with Terraform for infra",
        20: "Architecture Decision Records (ADRs) in docs/",
        21: "Performance under extreme load — need chaos testing",
        22: "Redis cluster failover behavior at scale",
        23: "Plugin system design — extensibility vs. complexity tradeoff",
        24: "Fibonacci story points (1, 2, 3, 5, 8)",
        25: "Go conventions (exported = PascalCase, unexported = camelCase)",
        26: "Performance benchmarks after every sprint, architecture reviews",
    },
    "expected": {
        "min_epics": 3,
        "max_epics": 6,
        "min_stories_per_epic": 2,
        "max_stories_per_epic": 5,
        "min_tasks_per_story": 2,
        "max_tasks_per_story": 5,
        "min_sprints": 1,
        "max_sprints": 4,
        "target_sprints": 3,
        "team_size": 3,
    },
}

# ---------------------------------------------------------------------------
# Dataset 5: ML Pipeline (data platform, cross-functional)
# ---------------------------------------------------------------------------

ML_PIPELINE = {
    "description": (
        "Build an end-to-end ML pipeline for customer churn prediction. "
        "Includes data ingestion from Snowflake, feature engineering, "
        "model training (XGBoost + neural nets), A/B testing framework, "
        "model serving via REST API, and monitoring dashboard for model drift."
    ),
    "answers": {
        1: "End-to-end ML pipeline for customer churn prediction with A/B testing and monitoring",
        2: "Hybrid — integrating with existing Snowflake data warehouse",
        3: "Data ingestion, feature engineering, model training, A/B testing, serving, drift monitoring",
        4: "Data scientists, ML engineers, product managers (dashboards)",
        5: "Production ML pipeline with automated retraining and drift alerts",
        6: "4 engineers",
        7: "2 ML engineers, 1 data engineer, 1 backend",
        8: "2-week sprints",
        9: "4 sprints (8 weeks)",
        10: "20 points per sprint",
        11: "Python, FastAPI, Airflow, Snowflake, XGBoost, PyTorch, Docker",
        12: "Batch pipeline (Airflow DAGs) + real-time serving (FastAPI)",
        13: "Snowflake, MLflow for experiment tracking, S3 for model artifacts",
        14: "Must process 10M rows daily, model latency < 100ms, HIPAA compliant",
        15: "Existing Snowflake warehouse and some notebooks",
        16: "Monorepo: pipeline/, serving/, dashboard/, notebooks/",
        17: "https://github.com/example/churn-ml",
        18: "pytest, Great Expectations for data validation",
        19: "GitHub Actions for CI, Airflow for orchestration",
        20: "Jupyter notebooks documenting EDA and model experiments",
        21: "Data quality from upstream sources — missing values, schema changes",
        22: "Snowflake query costs at scale, GPU availability for training",
        23: "Optimal model architecture unknown — need experimentation sprint",
        24: "Fibonacci story points (1, 2, 3, 5, 8)",
        25: "snake_case for Python, descriptive names for DAGs and features",
        26: "Weekly model performance reviews, experiment log in MLflow",
    },
    "expected": {
        "min_epics": 3,
        "max_epics": 6,
        "min_stories_per_epic": 2,
        "max_stories_per_epic": 5,
        "min_tasks_per_story": 2,
        "max_tasks_per_story": 5,
        "min_sprints": 2,
        "max_sprints": 5,
        "target_sprints": 4,
        "team_size": 4,
    },
}

# ---------------------------------------------------------------------------
# All datasets — iterable for parametrized tests
# ---------------------------------------------------------------------------

ALL_DATASETS = {
    "todo_app": TODO_APP,
    "saas_platform": SAAS_PLATFORM,
    "mobile_app": MOBILE_APP,
    "api_gateway": API_GATEWAY,
    "ml_pipeline": ML_PIPELINE,
}


# ---------------------------------------------------------------------------
# Helper: build a completed questionnaire from a dataset
# ---------------------------------------------------------------------------


def build_questionnaire(dataset: dict) -> QuestionnaireState:
    """Build a completed QuestionnaireState from a golden dataset's answers."""
    qs = QuestionnaireState(completed=True, current_question=TOTAL_QUESTIONS + 1)
    for q_num, answer in dataset["answers"].items():
        qs.answers[q_num] = answer
    return qs
